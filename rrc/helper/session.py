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
"""The RRC client state machine.

This module holds the protocol logic and nothing else: it never imports RNS and
never touches a socket. It is handed a ``send`` callable for outbound frames and
an ``emit`` callable for events destined for WeeChat, which makes the whole
state machine testable without a network. The Reticulum transport that supplies
those callables lives in :mod:`rrc.helper.link`.

Session shape follows 2-RRC §Sessions and First Contact: a Link is the session,
``HELLO`` is the first thing the client says, and the hub answers ``WELCOME``.
Nothing survives a Link drop. There is no history and no replay, so this module
deliberately does not buffer anything for later delivery.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Iterable

from . import constants as C
from . import envelope as E
from .limits import Limits

#: Client name and version advertised in the ``HELLO`` body.
CLIENT_NAME = "weechat-rrc"
CLIENT_VERSION = "0.1.1"

#: Capabilities this client advertises. Resource transfer is deliberately
#: absent: ``EX1`` says a client that does not want resources should simply not
#: advertise the capability.
CLIENT_CAPS = (C.CAP_ACTION, C.CAP_DIRECT_NOTICE)

#: Control characters stripped from anything the hub sends before it is shown.
#: A nickname is attacker-controlled text; without this it could forge buffer
#: lines or smuggle terminal escapes.
_CONTROL = dict.fromkeys(list(range(0x00, 0x20)) + [0x7F] + list(range(0x80, 0xA0)))

#: Message types that carry room or direct chat content.
_CHAT_TYPES = {
    C.T_MSG: "msg",
    C.T_NOTICE: "notice",
    C.T_ACTION: "action",
}


def sanitise(text: object, fallback: str | None = None) -> str | None:
    """Strip control characters from hub-supplied *text*.

    Args:
        text: A value received from the network, of any type.
        fallback: Returned when *text* is not a string.

    Returns:
        The cleaned string, or *fallback* when *text* was not text at all.
    """
    if not isinstance(text, str):
        return fallback
    return text.translate(_CONTROL)


def normalise_room(room: str) -> str:
    """Return *room* in the case-insensitive form used for local matching.

    3-RRC §Room Name Normalization says hubs normalise internally and that
    clients must not assume casing survives, so rooms are matched lowercased.
    """
    return room.strip().lower()


def parse_caps(value: object) -> set[int]:
    """Return the capability keys present in a ``HELLO``/``WELCOME`` body.

    3-RRC leaves the capability structure open: it may be a map of key to
    boolean, or a plain list of keys. Both are accepted, and anything else
    yields an empty set rather than an error.
    """
    if isinstance(value, dict):
        return {k for k, v in value.items() if isinstance(k, int) and v}
    if isinstance(value, (list, tuple, set)):
        return {k for k in value if isinstance(k, int) and not isinstance(k, bool)}
    return set()


class RRCSession:
    """Drives one RRC session over an already-established Link.

    The caller creates this when a Link comes up, feeds it inbound frames with
    :meth:`on_frame`, and calls the request methods to act on the user's behalf.
    Every user-visible consequence leaves through the ``emit`` callable as a
    plain dict, ready for the IPC layer to serialise.
    """

    def __init__(
        self,
        identity_hash: bytes,
        send: Callable[[bytes], None],
        emit: Callable[[dict[str, Any]], None],
        nick: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Create a session in its pre-``HELLO`` state.

        Args:
            identity_hash: This client's 16-byte Reticulum identity hash.
            send: Called with each encoded envelope to put on the Link.
            emit: Called with each event destined for WeeChat.
            nick: Advisory nickname, or ``None`` to send none.
            clock: Monotonic time source, injectable for tests.
            on_ready: Called once ``WELCOME`` has been accepted. Room joins
                must wait for it: 2-RRC allows a hub to answer anything sent
                before ``WELCOME`` with an error instead of acting on it.
        """
        self.identity_hash = identity_hash
        self.nick = nick
        self.rooms: set[str] = set()
        self.limits = Limits()
        self.hub_caps: set[int] = set()
        self.hub_name: str | None = None
        self.ready = False
        self._send = send
        self._emit = emit
        self._clock = clock
        self._pending_joins: set[str] = set()
        self._pending_parts: set[str] = set()
        self._ping_sent: dict[bytes, float] = {}
        self._sent_times: deque[float] = deque()
        self._on_ready = on_ready

    # -- outbound ---------------------------------------------------------

    def start(self) -> None:
        """Announce this client to the hub with a ``HELLO``.

        2-RRC: the first message a client sends should be a ``HELLO``, and a
        hub is entitled to be uncooperative if it is not.
        """
        body = {
            C.B_HELLO_NAME: CLIENT_NAME,
            C.B_HELLO_VER: CLIENT_VERSION,
            C.B_HELLO_CAPS: {cap: True for cap in CLIENT_CAPS},
        }
        self._transmit(C.T_HELLO, body=body)

    def join(self, room: str) -> None:
        """Request membership of *room*."""
        name = normalise_room(room)
        if name in self.rooms:
            return
        if complaint := self.limits.check("room", name):
            self._fail(complaint)
            return
        allowed = self.limits.max_rooms_per_session
        if allowed is not None and len(self.rooms) >= allowed:
            self._fail(f"this hub allows at most {allowed} rooms per session")
            return
        self._pending_joins.add(name)
        self._transmit(C.T_JOIN, room=name)

    def part(self, room: str) -> None:
        """Leave *room*."""
        name = normalise_room(room)
        self._pending_parts.add(name)
        self._transmit(C.T_PART, room=name)

    def say(self, room: str, text: str, kind: int = C.T_MSG) -> None:
        """Send room content as ``MSG``, ``NOTICE`` or ``ACTION``.

        Args:
            room: Destination room.
            text: Message body.
            kind: One of :data:`constants.T_MSG`, ``T_NOTICE`` or ``T_ACTION``.
        """
        name = normalise_room(room)
        if complaint := self.limits.check("body", text):
            self._fail(complaint)
            return
        if complaint := self._rate_complaint():
            self._fail(complaint)
            return
        self._transmit(kind, room=name, body=text)

    def action(self, room: str, text: str) -> None:
        """Send *text* to *room* as an ``ACTION``, the ``/me`` equivalent."""
        self.say(room, text, kind=C.T_ACTION)

    def direct(self, target: bytes, text: str) -> None:
        """Send a direct ``NOTICE`` to *target*'s identity hash.

        This is the ``EX1`` direct-delivery extension, not core RRC, so it is
        refused unless the hub advertised :data:`constants.CAP_DIRECT_NOTICE`.
        Failing loudly here is better than emitting an envelope the hub will
        silently drop.
        """
        if C.CAP_DIRECT_NOTICE not in self.hub_caps:
            self._fail("this hub does not support direct messages")
            return
        if len(target) != C.IDENTITY_HASH_BYTES:
            self._fail(
                f"a direct message needs a full {C.IDENTITY_HASH_BYTES}-byte "
                f"identity hash"
            )
            return
        if complaint := self.limits.check("body", text):
            self._fail(complaint)
            return
        self._transmit(C.T_NOTICE, body=text, dst=target)

    def set_nick(self, nick: str) -> None:
        """Change the advisory nickname sent with subsequent envelopes.

        1-RRC §Identity: a nickname is a label attached to a session, not
        identity. Nothing is sent to the hub; the new value simply rides along
        on the next envelope.
        """
        if complaint := self.limits.check("nick", nick):
            self._fail(complaint)
            return
        self.nick = nick
        self._emit({"op": "nick", "nick": nick})

    def ping(self) -> None:
        """Send a ``PING`` and start timing the round trip."""
        token = E.new_msg_id()
        self._ping_sent[token] = self._clock()
        self._transmit(C.T_PING, body=token)

    # -- inbound ----------------------------------------------------------

    def on_frame(self, raw: bytes) -> None:
        """Handle one frame received over the Link.

        Frames that :func:`envelope.decode` rejects are dropped silently, which
        covers malformed CBOR, foreign protocol versions and unknown message
        types in one place.
        """
        env = E.decode(raw)
        if env is None:
            return
        handler = {
            C.T_WELCOME: self._on_welcome,
            C.T_JOINED: self._on_joined,
            C.T_PARTED: self._on_parted,
            C.T_MSG: self._on_chat,
            C.T_NOTICE: self._on_chat,
            C.T_ACTION: self._on_chat,
            C.T_PING: self._on_ping,
            C.T_PONG: self._on_pong,
            C.T_ERROR: self._on_error,
        }.get(env.type)
        if handler is None:
            # A client-to-hub type arriving from the hub; 2-RRC says ignore it.
            return
        handler(env)

    def on_link_down(self, reason: str = "link closed") -> None:
        """Reset session state after the Link drops.

        1-RRC §Transport: membership evaporates with the Link and reconnecting
        creates a new session with no relationship to the previous one.
        """
        self.ready = False
        self.rooms.clear()
        self._pending_joins.clear()
        self._pending_parts.clear()
        self._ping_sent.clear()
        self._emit({"op": "state", "state": "down", "reason": reason})

    # -- inbound handlers -------------------------------------------------

    def _on_welcome(self, env: E.Envelope) -> None:
        """Record what the hub advertised and open the session for use.

        The hub's own identity is reported so clients can tell hub-generated
        notices apart from room members; a hub is not a member of the rooms it
        relays.
        """
        body = env.body if isinstance(env.body, dict) else {}
        self.hub_name = sanitise(body.get(C.B_WELCOME_HUB))
        self.hub_caps = parse_caps(body.get(C.B_WELCOME_CAPS))
        self.limits = Limits.from_welcome_body(body)
        self.ready = True
        self._emit(
            {
                "op": "welcome",
                "src": env.src.hex(),
                "hub": self.hub_name,
                "version": sanitise(body.get(C.B_WELCOME_VER)),
                "caps": sorted(self.hub_caps),
                "limits": self.limits.as_dict(),
            }
        )
        if self._on_ready is not None:
            self._on_ready()

    def _on_joined(self, env: E.Envelope) -> None:
        """Confirm our own join, or report someone else arriving."""
        if env.room is None:
            return
        room = normalise_room(env.room)
        members = [m.hex() for m in _hashes(env.body)]
        if room in self._pending_joins:
            self._pending_joins.discard(room)
            self.rooms.add(room)
            self._emit({"op": "joined", "room": room, "members": members})
            return
        self._emit(
            {
                "op": "join",
                "room": room,
                "members": members,
                "nick": sanitise(env.nick),
            }
        )

    def _on_parted(self, env: E.Envelope) -> None:
        """Confirm our own part, or report someone else leaving."""
        if env.room is None:
            return
        room = normalise_room(env.room)
        members = [m.hex() for m in _hashes(env.body)]
        if room in self._pending_parts:
            self._pending_parts.discard(room)
            self.rooms.discard(room)
            self._emit({"op": "parted", "room": room})
            return
        self._emit(
            {
                "op": "part",
                "room": room,
                "members": members,
                "nick": sanitise(env.nick),
            }
        )

    def _on_chat(self, env: E.Envelope) -> None:
        """Report room content, or a direct message addressed to us."""
        event = {
            "op": "chat",
            "kind": _CHAT_TYPES[env.type],
            "src": env.src.hex(),
            "nick": sanitise(env.nick),
            "body": sanitise(env.body, fallback=""),
        }
        if env.dst is not None:
            event["op"] = "direct"
        else:
            event["room"] = normalise_room(env.room) if env.room else None
        self._emit(event)

    def _on_ping(self, env: E.Envelope) -> None:
        """Answer a hub ``PING``, echoing its body unchanged as 3-RRC requires."""
        self._transmit(C.T_PONG, body=env.body)

    def _on_pong(self, env: E.Envelope) -> None:
        """Report the round-trip time for a ``PING`` we sent."""
        token = env.body if isinstance(env.body, bytes) else None
        started = self._ping_sent.pop(token, None) if token else None
        if started is None:
            return  # unsolicited or already-timed PONG
        lag_ms = int((self._clock() - started) * 1000)
        self._emit({"op": "pong", "lag_ms": lag_ms})

    def _on_error(self, env: E.Envelope) -> None:
        """Surface a hub error in plain language."""
        self._fail(sanitise(env.body, fallback="hub reported an error"))

    # -- internals --------------------------------------------------------

    def _transmit(
        self,
        msg_type: int,
        room: str | None = None,
        body: Any = None,
        dst: bytes | None = None,
    ) -> None:
        """Encode and send one envelope, attaching the advisory nickname."""
        self._send(
            E.encode(
                msg_type,
                src=self.identity_hash,
                room=room,
                body=body,
                nick=self.nick,
                dst=dst,
            )
        )

    def _rate_complaint(self) -> str | None:
        """Return a complaint if sending now would exceed the hub's rate limit."""
        limit = self.limits.rate_limit_msgs_per_minute
        now = self._clock()
        while self._sent_times and now - self._sent_times[0] >= 60.0:
            self._sent_times.popleft()
        if limit is not None and len(self._sent_times) >= limit:
            return (
                f"this hub allows {limit} messages per minute; "
                f"waiting avoids being disconnected"
            )
        self._sent_times.append(now)
        return None

    def _fail(self, message: str) -> None:
        """Emit a user-facing error without disturbing session state."""
        self._emit({"op": "error", "message": message})


def _hashes(body: object) -> Iterable[bytes]:
    """Yield the identity hashes in a ``JOINED``/``PARTED`` member list.

    ``EX1`` sends a list of identity hashes, but 2-RRC says the list is
    optional and advisory, so anything else yields nothing.
    """
    if not isinstance(body, (list, tuple)):
        return []
    return [m for m in body if isinstance(m, bytes)]
