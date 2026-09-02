# SPDX-FileCopyrightText: 2026 Afri Blank (@l5yth)
# SPDX-License-Identifier: Apache-2.0
#
# Copyright © 2026 Afri Blank (@l5yth)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Helper process entry point.

Reads newline-delimited JSON commands from stdin, drives an :class:`RRCSession`
over a Reticulum Link, and writes events back as newline-delimited JSON. The
WeeChat script on the other end of the pipe never imports Reticulum.

All session state is touched from a single thread. Commands arrive on a reader
thread and Reticulum callbacks arrive on RNS's own threads; both only enqueue,
and the main loop is the sole mutator.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from typing import Any, BinaryIO, Callable

from . import identity as identity_store
from . import ipc
from . import link as link_mod
from .session import RRCSession

#: Queue entries are ``(kind, payload)``; these are the kinds.
_CMD, _EOF, _UP, _FRAME, _DOWN, _RETRY = (
    "cmd",
    "eof",
    "up",
    "frame",
    "down",
    "retry",
)


def claim_stdout() -> BinaryIO:
    """Take exclusive ownership of stdout for the IPC stream.

    Reticulum logs to stdout by default, and any stray ``print`` in a
    dependency would corrupt the framing. This duplicates the real stdout for
    our own use, then points file descriptor 1 at stderr so everything else
    ends up on WeeChat's core buffer instead of in the protocol stream.

    Returns:
        An unbuffered binary stream to write IPC frames to.
    """
    stream = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    return stream


class Helper:
    """Wires the IPC stream to an RRC session and its Reticulum Link."""

    def __init__(
        self,
        out: BinaryIO,
        schedule: Callable[[float, Callable[[], None]], Any] | None = None,
    ) -> None:
        """Create a helper that writes events to *out*.

        Args:
            out: Binary stream for outbound IPC frames.
            schedule: Called as ``schedule(delay, fn)`` to run *fn* later; used
                for reconnect backoff. Defaults to a daemon timer thread.
        """
        self.out = out
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.session: RRCSession | None = None
        self.hub: link_mod.HubLink | None = None
        self.identity = None
        self.backoff = link_mod.Backoff()
        self.autojoin: list[str] = []
        self.dest_hash: bytes | None = None
        self.reconnect = True
        self._schedule = schedule or self._timer
        self._running = True

    # -- IPC --------------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        """Write one event to the IPC stream."""
        try:
            self.out.write(ipc.encode_frame(event))
        except (BrokenPipeError, ValueError):
            # WeeChat went away; the run loop will notice and stop.
            self._running = False

    def fail(self, message: str) -> None:
        """Report an error to the user without ending the session."""
        self.emit({"op": "error", "message": message})

    # -- command handling -------------------------------------------------

    def handle(self, cmd: dict[str, Any]) -> None:
        """Dispatch one command frame from WeeChat."""
        op = cmd.get("op")
        handlers = {
            "connect": self._cmd_connect,
            "disconnect": self._cmd_disconnect,
            "join": self._cmd_join,
            "part": self._cmd_part,
            "say": self._cmd_say,
            "direct": self._cmd_direct,
            "nick": self._cmd_nick,
            "ping": self._cmd_ping,
            "quit": self._cmd_quit,
        }
        handler = handlers.get(op)
        if handler is None:
            self.fail(f"unknown command {op!r}")
            return
        try:
            handler(cmd)
        except (link_mod.LinkError, identity_store.IdentityError) as exc:
            self.fail(str(exc))

    def _cmd_connect(self, cmd: dict[str, Any]) -> None:
        """Open a session with the hub named in *cmd*."""
        self.dest_hash = link_mod.parse_hub_hash(cmd.get("hub") or "")
        path = cmd.get("identity")
        self.identity = identity_store.load_or_create(
            path if path else identity_store.default_path()
        )
        self.autojoin = [r for r in cmd.get("autojoin") or [] if isinstance(r, str)]
        self.reconnect = bool(cmd.get("reconnect", True))
        self.session = RRCSession(
            self.identity.hash,
            send=self._send,
            emit=self.emit,
            nick=cmd.get("nick"),
            on_ready=self._join_autojoin,
        )
        self.emit({"op": "identity", "hash": self.identity.hash.hex()})
        self._open_link()

    def _cmd_disconnect(self, cmd: dict[str, Any]) -> None:
        """Close the session and stop reconnecting."""
        self.reconnect = False
        if self.hub is not None:
            self.hub.close()
            self.hub = None
        self.emit({"op": "state", "state": "down", "reason": "disconnected"})

    def _cmd_join(self, cmd: dict[str, Any]) -> None:
        """Join the room named in *cmd*."""
        self._require_session().join(cmd.get("room") or "")

    def _cmd_part(self, cmd: dict[str, Any]) -> None:
        """Leave the room named in *cmd*."""
        self._require_session().part(cmd.get("room") or "")

    def _cmd_say(self, cmd: dict[str, Any]) -> None:
        """Send room content as a message, notice, or action."""
        kinds = {"msg": 20, "notice": 21, "action": 22}
        kind = kinds.get(cmd.get("kind") or "msg")
        if kind is None:
            self.fail(f"unknown message kind {cmd.get('kind')!r}")
            return
        self._require_session().say(
            cmd.get("room") or "", cmd.get("text") or "", kind=kind
        )

    def _cmd_direct(self, cmd: dict[str, Any]) -> None:
        """Send a direct message to an identity hash."""
        target = ipc.from_hex(cmd.get("target"))
        if target is None:
            self.fail("a direct message needs a target identity hash")
            return
        self._require_session().direct(target, cmd.get("text") or "")

    def _cmd_nick(self, cmd: dict[str, Any]) -> None:
        """Change the advisory nickname."""
        self._require_session().set_nick(cmd.get("nick") or "")

    def _cmd_ping(self, cmd: dict[str, Any]) -> None:
        """Measure round-trip time to the hub."""
        self._require_session().ping()

    def _cmd_quit(self, cmd: dict[str, Any]) -> None:
        """Shut the helper down."""
        self.reconnect = False
        self._running = False

    def _require_session(self) -> RRCSession:
        """Return the live session, or raise a user-facing error."""
        if self.session is None:
            raise link_mod.LinkError("not connected to a hub")
        return self.session

    # -- link lifecycle ---------------------------------------------------

    def _open_link(self) -> None:
        """Start establishing a Link, reporting failure as an error event."""
        self.emit({"op": "state", "state": "connecting"})
        self.hub = link_mod.HubLink(
            self.dest_hash,
            self.identity,
            on_up=lambda: self.events.put((_UP, None)),
            on_frame=lambda data: self.events.put((_FRAME, data)),
            on_down=lambda reason: self.events.put((_DOWN, reason)),
        )
        try:
            self.hub.open()
        except link_mod.LinkError as exc:
            self.fail(str(exc))
            self._schedule_retry()

    def _send(self, data: bytes) -> None:
        """Put an encoded envelope on the Link, reporting a dead Link."""
        if self.hub is None:
            self.fail("not connected to a hub")
            return
        try:
            self.hub.send(data)
        except link_mod.LinkError as exc:
            self.fail(str(exc))

    def _on_up(self) -> None:
        """Announce ourselves once the Link is up.

        Only ``HELLO`` is sent here. Rooms are joined from
        :meth:`_join_autojoin`, which runs when ``WELCOME`` arrives, because a
        hub may reject anything sent before it has accepted the session.
        """
        self.backoff.reset()
        self.emit({"op": "state", "state": "up"})
        session = self.session
        if session is None:  # pragma: no cover - disconnect raced the callback
            return
        session.start()

    def _join_autojoin(self) -> None:
        """Join the configured rooms, once the hub has sent ``WELCOME``."""
        session = self.session
        if session is None:  # pragma: no cover - disconnect raced the callback
            return
        for room in self.autojoin:
            session.join(room)

    def _on_down(self, reason: str) -> None:
        """Reset the session and schedule a reconnect if one is wanted."""
        if self.session is not None:
            self.session.on_link_down(reason)
        self.hub = None
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        """Queue another connection attempt after the backoff delay."""
        if not self.reconnect:
            return
        delay = self.backoff.next_delay()
        self.emit({"op": "reconnect", "seconds": delay})
        self._schedule(delay, lambda: self.events.put((_RETRY, None)))

    @staticmethod
    def _timer(delay: float, fn: Callable[[], None]) -> threading.Timer:
        """Run *fn* after *delay* seconds on a daemon timer thread."""
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        return timer

    # -- main loop --------------------------------------------------------

    def read_commands(self, stream: BinaryIO) -> None:
        """Feed IPC frames from *stream* into the event queue until EOF.

        Reads with ``read1`` rather than ``read``: on a pipe, ``read(n)`` blocks
        until it has all *n* bytes or the writer closes, which would stall every
        command until WeeChat exited. ``read1`` returns as soon as anything is
        available. Streams without it, such as plain files, fall back to
        ``read``, where the distinction does not arise.
        """
        reader = ipc.FrameReader()
        read = getattr(stream, "read1", stream.read)
        while True:
            chunk = read(4096)
            if not chunk:
                break
            for frame in reader.feed(chunk):
                self.events.put((_CMD, frame))
        self.events.put((_EOF, None))

    def run(self, stream: BinaryIO) -> int:
        """Read commands from *stream* and process events until shutdown.

        Returns:
            A process exit status: always ``0``, since a closed pipe is the
            normal way for the helper to end.
        """
        thread = threading.Thread(
            target=self.read_commands, args=(stream,), daemon=True
        )
        thread.start()
        while self._running:
            kind, payload = self.events.get()
            if kind == _CMD:
                self.handle(payload)
            elif kind == _FRAME:
                if self.session is not None:
                    self.session.on_frame(payload)
            elif kind == _UP:
                self._on_up()
            elif kind == _DOWN:
                self._on_down(payload)
            elif kind == _RETRY:
                self._open_link()
            else:
                break  # EOF: WeeChat closed the pipe
        if self.hub is not None:
            self.hub.close()
        return 0


def main(argv: list[str] | None = None) -> int:
    """Run the helper against stdin and stdout.

    Args:
        argv: Unused; accepted so the console-script entry point matches the
            conventional signature.

    Returns:
        The process exit status.
    """
    out = claim_stdout()
    link_mod.RNS.Reticulum()
    return Helper(out).run(sys.stdin.buffer)


if __name__ == "__main__":
    # The stdin reader is a daemon thread that may be blocked inside a read
    # when the loop ends. Normal interpreter shutdown then tries to close that
    # buffer, cannot take its lock, and aborts with a fatal error. Leaving via
    # os._exit skips that teardown; there is nothing left to flush, because the
    # IPC stream is unbuffered.
    os._exit(main())
