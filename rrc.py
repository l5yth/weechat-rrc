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
"""Reticulum Relay Chat client for WeeChat.

This script imports nothing outside the standard library. Everything that
touches Reticulum runs in a separate helper process, which this script spawns
and talks to over a pipe using newline-delimited JSON. That keeps a blocking or
crashing RNS call from freezing the WeeChat UI, and it keeps ``/script reload``
safe, because ``RNS.Reticulum()`` is a process-wide singleton that cannot be
re-initialised.

Reticulum configuration is out of scope: a running shared instance is assumed
and is never created or modified from here.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess

import weechat

SCRIPT_NAME = "rrc"
SCRIPT_AUTHOR = "Afri Blank (@l5yth)"
SCRIPT_VERSION = "0.1.0"
SCRIPT_LICENSE = "Apache-2.0"
SCRIPT_DESC = "Reticulum Relay Chat (RRC) client"

#: Configuration options and their defaults, created on first load.
DEFAULTS = {
    "helper.python": "",
    "identity.path": "",
    "reconnect": "on",
    "autojoin": "",
}

#: Import check a candidate interpreter must pass to run the helper.
PROBE = "import RNS, cbor2"

#: Interpreters tried when ``helper.python`` is not configured, in order.
FALLBACK_PYTHONS = ("python3", "~/.venv/bin/python")

#: Live connections, keyed by the short hub name shown in buffer names.
connections: dict[str, "Connection"] = {}


# -- helper interpreter discovery -----------------------------------------


def probe_python(path: str) -> bool:
    """Return ``True`` if *path* is an interpreter that can run the helper.

    Both Reticulum and cbor2 must be importable; a Python with only one of them
    would fail later and less clearly.
    """
    try:
        return (
            subprocess.run(
                [os.path.expanduser(path), "-c", PROBE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def find_python() -> str | None:
    """Return the first interpreter able to run the helper, or ``None``.

    Resolution order is the ``helper.python`` option, then ``$RRC_PYTHON``,
    then the fallbacks. Nothing is hardcoded: on a machine where Reticulum is
    installed system-wide, the first fallback wins.
    """
    candidates = [
        weechat.config_get_plugin("helper.python"),
        os.environ.get("RRC_PYTHON", ""),
        *FALLBACK_PYTHONS,
    ]
    for candidate in candidates:
        if candidate and probe_python(candidate):
            return os.path.expanduser(candidate)
    return None


def set_nonblocking(stream) -> None:
    """Put *stream* into non-blocking mode.

    ``hook_fd`` fires when a descriptor becomes readable, but a blocking read
    would still stall if the data were consumed elsewhere or arrived as a
    partial line. These reads happen on WeeChat's main thread, so blocking
    here would freeze the entire user interface.
    """
    descriptor = stream.fileno()
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    fcntl.fcntl(descriptor, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def helper_directory() -> str:
    """Return the directory containing the ``rrc_helper`` package.

    The helper lives beside this script. ``__file__`` is set when WeeChat loads
    a script, but falling back to the WeeChat data directory keeps the script
    working if that ever stops being true.
    """
    here = globals().get("__file__")
    if here:
        return os.path.dirname(os.path.abspath(here))
    return os.path.join(weechat.info_get("weechat_dir", ""), "python")


# -- display helpers -------------------------------------------------------


def log(message: str) -> None:
    """Print a script-level message on WeeChat's core buffer."""
    weechat.prnt("", f"{SCRIPT_NAME}: {message}")


def clean(text: object, fallback: str = "") -> str:
    """Return *text* with control characters removed, for safe display.

    Everything the hub sends is attacker-controlled. The helper already strips
    control characters, but this is the last point before the text reaches a
    buffer, so it is enforced here too rather than assumed.
    """
    if not isinstance(text, str):
        return fallback
    return "".join(ch for ch in text if ch.isprintable() or ch == " ")


def short(identity_hash: str) -> str:
    """Return an abbreviated identity hash, for use when no nickname exists."""
    return identity_hash[:8] if identity_hash else "?"


def speaker(event: dict) -> str:
    """Return the display name for a message's sender.

    A nickname is advisory, so the authoritative short identity hash is shown
    whenever no nickname was supplied.
    """
    return clean(event.get("nick")) or short(event.get("src", ""))


class Connection:
    """One hub: its helper process, its buffers, and its event handling."""

    def __init__(self, name: str, hub_hash: str, nick: str = "") -> None:
        """Prepare a connection to *hub_hash* to be shown as *name*."""
        self.name = name
        self.hub_hash = hub_hash
        self.nick = nick
        self.hub_name = ""
        self.process: subprocess.Popen | None = None
        self.identity = ""
        self.lag = ""
        self.state = "disconnected"
        self.rooms: dict[str, str] = {}
        self.hooks: list[str] = []
        self._reader = None
        self.buffer = weechat.buffer_new(
            f"{SCRIPT_NAME}.{name}", "rrc_input_cb", name, "rrc_close_cb", name
        )
        weechat.buffer_set(self.buffer, "title", f"RRC hub {hub_hash}")
        weechat.buffer_set(self.buffer, "localvar_set_type", "server")
        weechat.buffer_set(self.buffer, "localvar_set_server", name)

    # -- process lifecycle ------------------------------------------------

    def start(self) -> bool:
        """Spawn the helper and begin a session. Returns success."""
        python = find_python()
        if python is None:
            self.display(
                "no Python with Reticulum and cbor2 was found. Set one with: "
                f"/set plugins.var.python.{SCRIPT_NAME}.helper.python "
                "/path/to/python"
            )
            return False
        directory = helper_directory()
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [directory, env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        try:
            self.process = subprocess.Popen(
                [python, "-m", "rrc_helper"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=directory,
                env=env,
            )
        except OSError as exc:
            self.display(f"could not start the helper: {exc}")
            return False
        self._reader = FrameReader()
        for stream, callback in (
            (self.process.stdout, "rrc_stdout_cb"),
            (self.process.stderr, "rrc_stderr_cb"),
        ):
            set_nonblocking(stream)
            self.hooks.append(
                weechat.hook_fd(stream.fileno(), 1, 0, 0, callback, self.name)
            )
        self.send(
            {
                "op": "connect",
                "hub": self.hub_hash,
                "nick": self.nick or None,
                "identity": weechat.config_get_plugin("identity.path") or None,
                "autojoin": self.autojoin(),
                "reconnect": weechat.config_get_plugin("reconnect") != "off",
            }
        )
        return True

    def autojoin(self) -> list[str]:
        """Return the rooms to rejoin automatically after each ``WELCOME``."""
        raw = weechat.config_get_plugin("autojoin")
        return [room.strip() for room in raw.split(",") if room.strip()]

    def stop(self) -> None:
        """Shut the helper down and release its hooks."""
        for hook in self.hooks:
            weechat.unhook(hook)
        self.hooks = []
        if self.process is not None:
            self.send({"op": "quit"})
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.kill()
            self.process = None
        self.state = "disconnected"

    def send(self, command: dict) -> None:
        """Write one command frame to the helper."""
        if self.process is None or self.process.stdin is None:
            self.display("not connected")
            return
        try:
            self.process.stdin.write(
                (json.dumps(command, separators=(",", ":")) + "\n").encode("utf-8")
            )
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.display("the helper is gone; use /rrc connect to restart it")
            self.process = None

    # -- buffers ----------------------------------------------------------

    def display(self, text: str, prefix: str = "") -> None:
        """Print a line on the hub's server buffer."""
        weechat.prnt(self.buffer, f"{prefix}\t{text}")

    def room_buffer(self, room: str) -> str:
        """Return the buffer for *room*, creating it if necessary."""
        if room in self.rooms:
            return self.rooms[room]
        pointer = weechat.buffer_new(
            f"{SCRIPT_NAME}.{self.name}.{room}",
            "rrc_input_cb",
            f"{self.name}/{room}",
            "rrc_close_cb",
            f"{self.name}/{room}",
        )
        weechat.buffer_set(pointer, "short_name", room)
        weechat.buffer_set(pointer, "localvar_set_type", "channel")
        weechat.buffer_set(pointer, "localvar_set_server", self.name)
        weechat.buffer_set(pointer, "localvar_set_channel", room)
        weechat.buffer_set(pointer, "nicklist", "1")
        self.rooms[room] = pointer
        return pointer

    def say(self, room: str, text: str) -> None:
        """Send *text* to *room*.

        Nothing is echoed locally: the hub sends every message back to its
        author, so a local echo would show each line twice.
        """
        self.send({"op": "say", "room": room, "text": text})

    # -- inbound events ---------------------------------------------------

    def drain(self, chunk: bytes) -> None:
        """Feed helper output through the framer and dispatch each event."""
        for event in self._reader.feed(chunk):
            self.on_event(event)

    def on_event(self, event: dict) -> None:
        """Route one event from the helper to its handler."""
        handler = {
            "identity": self._ev_identity,
            "state": self._ev_state,
            "welcome": self._ev_welcome,
            "error": self._ev_error,
            "reconnect": self._ev_reconnect,
            "joined": self._ev_joined,
            "parted": self._ev_parted,
            "join": self._ev_join,
            "part": self._ev_part,
            "chat": self._ev_chat,
            "direct": self._ev_direct,
            "pong": self._ev_pong,
            "nick": self._ev_nick,
        }.get(event.get("op"))
        if handler is not None:
            handler(event)

    def _ev_identity(self, event: dict) -> None:
        """Record and show the identity this session presents to the hub."""
        self.identity = event.get("hash", "")
        self.display(f"your identity is {self.identity}", "--")

    def _ev_state(self, event: dict) -> None:
        """Report a connection state change."""
        self.state = event.get("state", "")
        reason = event.get("reason")
        self.display(f"{self.state}" + (f" ({clean(reason)})" if reason else ""), "--")

    def _ev_welcome(self, event: dict) -> None:
        """Show the hub's greeting line once the session is open."""
        self.hub_name = clean(event.get("hub"), "unnamed hub")
        version = clean(event.get("version"))
        suffix = f" ({version})" if version else ""
        self.display(f"connected to {self.hub_name}{suffix}", "--")
        weechat.buffer_set(self.buffer, "title", f"{self.hub_name} — {self.hub_hash}")

    def _ev_error(self, event: dict) -> None:
        """Show an error from the helper or the hub."""
        self.display(clean(event.get("message"), "unknown error"), "=!=")

    def _ev_reconnect(self, event: dict) -> None:
        """Announce the delay before the next connection attempt."""
        self.display(f"reconnecting in {event.get('seconds')}s", "--")

    def _ev_joined(self, event: dict) -> None:
        """Open the room buffer after our own join is confirmed."""
        room = clean(event.get("room"))
        buffer = self.room_buffer(room)
        members = event.get("members") or []
        weechat.prnt(buffer, f"--\tjoined {room} ({len(members)} present)")

    def _ev_parted(self, event: dict) -> None:
        """Report that we left a room."""
        room = clean(event.get("room"))
        if room in self.rooms:
            weechat.prnt(self.rooms[room], f"--\tleft {room}")

    def _ev_join(self, event: dict) -> None:
        """Report somebody else arriving in a room."""
        room = clean(event.get("room"))
        for member in event.get("members") or []:
            name = clean(event.get("nick")) or short(member)
            weechat.prnt(self.room_buffer(room), f"-->\t{name} joined {room}")

    def _ev_part(self, event: dict) -> None:
        """Report somebody else leaving a room."""
        room = clean(event.get("room"))
        if room not in self.rooms:
            return
        for member in event.get("members") or []:
            name = clean(event.get("nick")) or short(member)
            weechat.prnt(self.rooms[room], f"<--\t{name} left {room}")

    def _ev_chat(self, event: dict) -> None:
        """Render room content, or a hub-wide notice with no room."""
        room = event.get("room")
        target = self.room_buffer(clean(room)) if room else self.buffer
        body = clean(event.get("body"))
        name = speaker(event)
        kind = event.get("kind")
        if kind == "action":
            weechat.prnt(target, f" *\t{name} {body}")
        elif kind == "notice":
            weechat.prnt(target, f"--\t{body}" if not room else f"--\t{name}: {body}")
        else:
            weechat.prnt(target, f"{name}\t{body}")

    def _ev_direct(self, event: dict) -> None:
        """Show a direct message on the server buffer for now."""
        self.display(f"{speaker(event)}: {clean(event.get('body'))}", "DM")

    def _ev_pong(self, event: dict) -> None:
        """Record the measured round-trip time."""
        self.lag = f"{event.get('lag_ms')}ms"
        self.display(f"lag is {self.lag}", "--")

    def _ev_nick(self, event: dict) -> None:
        """Record a confirmed nickname change."""
        self.nick = clean(event.get("nick"))
        self.display(f"you are now known as {self.nick}", "--")


class FrameReader:
    """Reassembles newline-delimited JSON frames arriving from the helper.

    A copy of the helper's reader, kept here so this script depends on nothing
    outside the standard library. Malformed frames are dropped rather than
    raised, so a corrupt stream cannot break the WeeChat callback.
    """

    def __init__(self) -> None:
        """Start with an empty buffer."""
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict]:
        """Return every complete frame that *chunk* completed."""
        self._buffer.extend(chunk)
        frames = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                return frames
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(frame, dict):
                frames.append(frame)


# -- WeeChat callbacks, resolved by name -----------------------------------


def find_connection(buffer: str) -> tuple["Connection | None", str]:
    """Return the connection and room that *buffer* belongs to.

    Returns ``(None, "")`` for any buffer this script does not own, which is
    how the command hooks decide whether to act or stand aside.
    """
    for connection in connections.values():
        if buffer == connection.buffer:
            return connection, ""
        for room, pointer in connection.rooms.items():
            if buffer == pointer:
                return connection, room
    return None, ""


def current_connection(buffer: str) -> tuple["Connection | None", str]:
    """Return the connection to act on for a command issued in *buffer*.

    Falls back to the only open connection when *buffer* is not one of this
    script's own. That makes ``/rrc join`` work from the core buffer, and from
    a command scheduled with ``/wait``, which WeeChat runs on the buffer where
    the wait was issued rather than the current one. With several connections
    open the choice is ambiguous, so the caller is asked to name one.
    """
    connection, room = find_connection(buffer)
    if connection is not None:
        return connection, room
    if len(connections) == 1:
        return next(iter(connections.values())), ""
    return None, ""


def rrc_stdout_cb(name: str, fd: str) -> int:
    """Read helper output and dispatch the events it completed."""
    connection = connections.get(name)
    if connection is None or connection.process is None:
        return weechat.WEECHAT_RC_OK
    chunk = _read_available(connection.process.stdout)
    if chunk:
        connection.drain(chunk)
    return weechat.WEECHAT_RC_OK


def rrc_stderr_cb(name: str, fd: str) -> int:
    """Surface helper diagnostics on the core buffer."""
    connection = connections.get(name)
    if connection is None or connection.process is None:
        return weechat.WEECHAT_RC_OK
    chunk = _read_available(connection.process.stderr)
    for line in chunk.decode("utf-8", "replace").splitlines():
        if line.strip():
            log(f"[{name}] {line}")
    return weechat.WEECHAT_RC_OK


def _read_available(stream) -> bytes:
    """Return whatever is readable on *stream* right now, without blocking."""
    try:
        return os.read(stream.fileno(), 65536)
    except (BlockingIOError, OSError, ValueError):
        return b""


def rrc_input_cb(data: str, buffer: str, text: str) -> int:
    """Send a line typed in a room buffer to that room."""
    name, _, room = data.partition("/")
    connection = connections.get(name)
    if connection is None:
        return weechat.WEECHAT_RC_OK
    if not room:
        connection.display("this is the hub buffer; type in a room buffer", "=!=")
        return weechat.WEECHAT_RC_OK
    connection.say(room, text)
    return weechat.WEECHAT_RC_OK


def rrc_close_cb(data: str, buffer: str) -> int:
    """Part the room, or disconnect entirely, when a buffer is closed."""
    name, _, room = data.partition("/")
    connection = connections.get(name)
    if connection is None:
        return weechat.WEECHAT_RC_OK
    if room:
        connection.rooms.pop(room, None)
        connection.send({"op": "part", "room": room})
    else:
        connection.stop()
        connections.pop(name, None)
    return weechat.WEECHAT_RC_OK


def rrc_command_cb(data: str, buffer: str, args: str) -> int:
    """Handle ``/rrc`` and its subcommands."""
    parts = args.split()
    if not parts:
        return _cmd_list()
    subcommand, rest = parts[0], parts[1:]
    handler = {
        "connect": _cmd_connect,
        "disconnect": _cmd_disconnect,
        "list": lambda a, b: _cmd_list(),
        "status": _cmd_status,
        "join": _cmd_join,
        "part": _cmd_part,
        "nick": _cmd_nick,
        "ping": _cmd_ping,
    }.get(subcommand)
    if handler is None:
        log(f"unknown command: {subcommand}. Try /help {SCRIPT_NAME}")
        return weechat.WEECHAT_RC_ERROR
    return handler(rest, buffer)


def _cmd_connect(args: list[str], buffer: str) -> int:
    """Open a connection to a hub."""
    if not args:
        log("usage: /rrc connect <hub-hash> [-nick <nick>]")
        return weechat.WEECHAT_RC_ERROR
    hub_hash = args[0].strip().lower()
    nick = ""
    if "-nick" in args:
        index = args.index("-nick")
        if index + 1 < len(args):
            nick = args[index + 1]
    name = hub_hash[:8]
    if name in connections:
        log(f"already connected to {name}; use /rrc disconnect {name} first")
        return weechat.WEECHAT_RC_ERROR
    connection = Connection(name, hub_hash, nick)
    connections[name] = connection
    if not connection.start():
        connections.pop(name, None)
        return weechat.WEECHAT_RC_ERROR
    return weechat.WEECHAT_RC_OK


def _cmd_disconnect(args: list[str], buffer: str) -> int:
    """Close one connection, or the one owning the current buffer."""
    connection = _target(args, buffer)
    if connection is None:
        return weechat.WEECHAT_RC_ERROR
    connection.stop()
    connections.pop(connection.name, None)
    log(f"disconnected from {connection.name}")
    return weechat.WEECHAT_RC_OK


def _cmd_list() -> int:
    """List every open connection."""
    if not connections:
        log("no connections. Use /rrc connect <hub-hash>")
        return weechat.WEECHAT_RC_OK
    for connection in connections.values():
        rooms = ", ".join(sorted(connection.rooms)) or "no rooms"
        log(f"{connection.name}: {connection.state}, {rooms}")
    return weechat.WEECHAT_RC_OK


def _cmd_status(args: list[str], buffer: str) -> int:
    """Report the state of one connection in detail."""
    connection = _target(args, buffer)
    if connection is None:
        return weechat.WEECHAT_RC_ERROR
    connection.display(f"hub      {connection.hub_name or '(unknown)'}", "--")
    connection.display(f"address  {connection.hub_hash}", "--")
    connection.display(f"identity {connection.identity or '(none)'}", "--")
    connection.display(f"state    {connection.state}", "--")
    connection.display(f"lag      {connection.lag or '(not measured)'}", "--")
    connection.display(
        f"rooms    {', '.join(sorted(connection.rooms)) or '(none)'}", "--"
    )
    return weechat.WEECHAT_RC_OK


def _cmd_join(args: list[str], buffer: str) -> int:
    """Join a room on the current connection."""
    connection, _ = current_connection(buffer)
    if connection is None or not args:
        log("usage: /rrc join <#room> (with a connection open)")
        return weechat.WEECHAT_RC_ERROR
    connection.send({"op": "join", "room": args[0]})
    return weechat.WEECHAT_RC_OK


def _cmd_part(args: list[str], buffer: str) -> int:
    """Leave a room, defaulting to the current room buffer."""
    connection, room = current_connection(buffer)
    if connection is None:
        log("usage: /rrc part [#room] (with a connection open)")
        return weechat.WEECHAT_RC_ERROR
    target = args[0] if args else room
    if not target:
        connection.display("which room? use /rrc part <#room>", "=!=")
        return weechat.WEECHAT_RC_ERROR
    connection.send({"op": "part", "room": target})
    return weechat.WEECHAT_RC_OK


def _cmd_nick(args: list[str], buffer: str) -> int:
    """Change the advisory nickname on the current connection."""
    connection, _ = current_connection(buffer)
    if connection is None or not args:
        log("usage: /rrc nick <nickname> (with a connection open)")
        return weechat.WEECHAT_RC_ERROR
    connection.send({"op": "nick", "nick": args[0]})
    return weechat.WEECHAT_RC_OK


def _cmd_ping(args: list[str], buffer: str) -> int:
    """Measure the round-trip time to the hub."""
    connection = _target(args, buffer)
    if connection is None:
        return weechat.WEECHAT_RC_ERROR
    connection.send({"op": "ping"})
    return weechat.WEECHAT_RC_OK


def _target(args: list[str], buffer: str) -> "Connection | None":
    """Return the connection named in *args*, or the current buffer's one."""
    if args:
        connection = connections.get(args[0])
        if connection is None:
            log(f"no such connection: {args[0]}")
        return connection
    connection, _ = current_connection(buffer)
    if connection is None:
        log("name a connection, or see /rrc list")
    return connection


def rrc_unload_cb() -> int:
    """Shut every helper down when the script is unloaded.

    This is what makes ``/script reload`` safe: each helper owns its own
    Reticulum instance and dies with its process, so nothing is left behind to
    collide with the reloaded script.
    """
    for connection in list(connections.values()):
        connection.stop()
    connections.clear()
    return weechat.WEECHAT_RC_OK


def main() -> None:
    """Register the script, seed configuration, and install the hooks."""
    if not weechat.register(
        SCRIPT_NAME,
        SCRIPT_AUTHOR,
        SCRIPT_VERSION,
        SCRIPT_LICENSE,
        SCRIPT_DESC,
        "rrc_unload_cb",
        "UTF-8",
    ):
        return
    for option, default in DEFAULTS.items():
        if not weechat.config_is_set_plugin(option):
            weechat.config_set_plugin(option, default)
    weechat.hook_command(
        SCRIPT_NAME,
        SCRIPT_DESC,
        "connect <hub-hash> [-nick <nick>] || disconnect [<name>] || list "
        "|| status [<name>] || join <#room> || part [<#room>] "
        "|| nick <nickname> || ping [<name>]",
        "  connect: open a session with a hub, named by its destination hash\n"
        "     -nick: advisory nickname; it is a label, never identity\n"
        "disconnect: close a session and stop reconnecting\n"
        "      list: show every open connection\n"
        "    status: show hub, identity, state, lag and rooms\n"
        "      join: enter a room\n"
        "      part: leave a room, defaulting to the current one\n"
        "      nick: change the advisory nickname\n"
        "      ping: measure round-trip time to the hub\n\n"
        "A running Reticulum shared instance is assumed; this script never\n"
        "reads or writes your Reticulum configuration.",
        "connect || disconnect || list || status || join || part || nick || ping",
        "rrc_command_cb",
        "",
    )


if __name__ == "__main__":
    main()
