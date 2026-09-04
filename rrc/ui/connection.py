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
"""One hub: its helper process, its buffers, and its event handling.

This is the stateful half of the WeeChat side. It owns the child process, the
buffers, the nicklist, the mention keys, and the dispatch of everything the
helper reports. It imports :mod:`rrc.ui.interpreter` and :mod:`rrc.ui.render`
as modules rather than by name, so that a test patching one of their functions
is seen here too.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess

import weechat

from rrc.ui import DM_PREFIX, SCRIPT_NAME
from rrc.ui import interpreter, render
from rrc.ui.render import (
    NOTIFY_MESSAGE,
    NOTIFY_PRIVATE,
    NO_HIGHLIGHT,
    RESTRICT_HUB,
    RESTRICT_PRIVATE,
    RESTRICT_ROOM,
    SELF_TAGS,
    TAG_ACTION,
    TAG_DIRECT,
    TAG_JOIN,
    TAG_MSG,
    TAG_NOTICE,
    TAG_PART,
    TAG_STATUS,
    clean,
    coloured,
    mention_key,
    short,
    show,
    speaker,
)

#: A hub notice listing a room's members, as rrcd answers "/who". The room is
#: taken from the text because such a notice may carry no room field.
MEMBER_LIST = re.compile(r"^members in (\S+):\s*(.+)$", re.DOTALL)

#: One "(identity)" in that listing. Hashes are abbreviated, so they are
#: matched as a prefix of a full one. Requiring hex keeps a parenthesised part
#: of somebody's name, such as "Flo (floscodes)", from being read as an id.
MEMBER_ENTRY = re.compile(r"\(([0-9a-f]{6,32})\)")


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


class Connection:
    """One hub: its helper process, its buffers, and its event handling."""

    def __init__(self, name: str, hub_hash: str, nick: str = "") -> None:
        """Prepare a connection to *hub_hash* to be shown as *name*."""
        self.name = name
        self.hub_hash = hub_hash
        self.nick = nick
        self.hub_name = ""
        self.hub_identity = ""
        self.process: subprocess.Popen | None = None
        self.identity = ""
        self.lag = ""
        self.state = "disconnected"
        self.rooms: dict[str, str] = {}
        self.members: dict[str, dict[str, str]] = {}
        self.dms: dict[str, str] = {}
        #: The mention keys currently published on every buffer we own, so the
        #: previous set can be retired by name when one of them changes.
        self.seeded: list[str] = []
        self.hooks: list[str] = []
        self._reader = None
        # Reuse the buffer if one is already open under this name. Closing a
        # session leaves its buffer behind so the scrollback survives, exactly
        # as the irc plugin does, and WeeChat refuses to create a second buffer
        # with the same name: it returns an empty pointer, and everything then
        # printed to it lands on the core buffer instead.
        self.buffer = weechat.buffer_search(
            "python", f"{SCRIPT_NAME}.{name}"
        ) or weechat.buffer_new(
            f"{SCRIPT_NAME}.{name}", "rrc_input_cb", name, "rrc_close_cb", name
        )
        weechat.buffer_set(self.buffer, "title", f"RRC hub {hub_hash}")
        weechat.buffer_set(self.buffer, "localvar_set_type", "server")
        weechat.buffer_set(self.buffer, "localvar_set_server", name)
        self.prepare(self.buffer, RESTRICT_HUB)
        self.publish_mentions()

    # -- mentions ---------------------------------------------------------

    def all_buffers(self) -> list[str]:
        """Return every buffer this connection owns."""
        return [self.buffer, *self.rooms.values(), *self.dms.values()]

    def mention_keys(self) -> list[str]:
        """Return the strings that mean "you" on this connection.

        Three of them (``SPEC.md`` D24): the full identity hash, its short form
        -- which is what renders when there is no nickname, so it is what a
        person can see and type -- and the advisory nickname. Each is
        sanitised, and any that reduces to nothing is dropped rather than
        seeded as an empty word.
        """
        keys: list[str] = []
        forms = (
            self.identity,
            short(self.identity) if self.identity else "",
            self.nick,
        )
        for candidate in forms:
            key = mention_key(candidate)
            if key and key not in keys:
                keys.append(key)
        return keys

    def prepare(self, buffer: str, restrict: str) -> None:
        """Give *buffer* its two highlight rules: what may mention, and with what.

        Args:
            buffer: The buffer to prepare.
            restrict: The tags whose lines may highlight there, one of the
                ``RESTRICT_*`` lists.
        """
        weechat.buffer_set(buffer, "highlight_tags_restrict", restrict)
        self.seed_mentions(buffer)

    def seed_mentions(self, buffer: str) -> None:
        """Give one buffer the mention keys already in force.

        Called when a buffer is opened. The ``irc`` plugin can seed at creation
        alone because it knows the user's nickname before the buffer exists;
        here the identity arrives from the helper after the hub buffer is
        already open, so creation-time seeding is only half the job and
        :meth:`publish_mentions` does the other half.
        """
        if self.seeded:
            weechat.buffer_set(buffer, "highlight_words_add", ",".join(self.seeded))

    def publish_mentions(self) -> None:
        """Bring every buffer into line after a mention key changed.

        Words are added and retired by name rather than by assigning
        ``highlight_words`` wholesale, so a word the user added themselves with
        ``/buffer setauto highlight_words_add`` is never discarded
        (``SPEC.md`` D24). WeeChat does the matching; this only publishes the
        strings to match on.
        """
        keys = self.mention_keys()
        if keys == self.seeded:
            return
        for buffer in self.all_buffers():
            if self.seeded:
                weechat.buffer_set(buffer, "highlight_words_del", ",".join(self.seeded))
            if keys:
                weechat.buffer_set(buffer, "highlight_words_add", ",".join(keys))
        self.seeded = keys

    # -- process lifecycle ------------------------------------------------

    def start(self) -> bool:
        """Spawn the helper and begin a session. Returns success."""
        python = interpreter.find_python()
        if python is None:
            for line in interpreter.missing_python_help():
                self.display(line, "=!=")
            return False
        directory = interpreter.helper_directory()
        if not os.path.isdir(os.path.join(directory, "rrc", "helper")):
            for line in interpreter.missing_helper_help(directory):
                self.display(line, "=!=")
            return False
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [directory, env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        try:
            self.process = subprocess.Popen(
                [python, "-m", "rrc.helper"],
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
        """Return the configured rooms that seed the helper's rejoin set."""
        raw = weechat.config_get_plugin("autojoin")
        return [room.strip() for room in raw.split(",") if room.strip()]

    def stop(self) -> None:
        """Shut the helper down and release its hooks."""
        for hook in self.hooks:
            weechat.unhook(hook)
        self.hooks = []
        process = self.process
        if process is not None:
            # send() clears self.process when the pipe is already broken, so
            # the local reference is what the teardown below must use.
            self.send({"op": "quit"})
            self.process = None
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.kill()
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
        show(self.buffer, f"{prefix}\t{text}", TAG_STATUS)

    def room_buffer(self, room: str) -> str:
        """Return the buffer for *room*, creating it if necessary."""
        if room in self.rooms:
            return self.rooms[room]
        name = f"{SCRIPT_NAME}.{self.name}.{room}"
        existing = weechat.buffer_search("python", name)
        if existing:
            self.rooms[room] = existing
            self.prepare(existing, RESTRICT_ROOM)
            return existing
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
        self.prepare(pointer, RESTRICT_ROOM)
        self.rooms[room] = pointer
        return pointer

    def say(self, room: str, text: str) -> None:
        """Send *text* to *room*.

        A leading ``//`` is WeeChat's escape for a line that should start with
        a slash. One slash is stripped, so typing ``//who`` sends the literal
        ``/who``, which hubs such as rrcd parse as a hub command. This is how
        hub commands reach the hub without this script keeping a list of them.

        Nothing is echoed locally: the hub sends every message back to its
        author, so a local echo would show each line twice.
        """
        if text.startswith("//"):
            text = text[1:]
        self.send({"op": "say", "room": room, "text": text})

    def who(self, room: str) -> None:
        """Ask the hub who is in *room*, unless the user turned that off.

        ``JOINED`` carries identity hashes and nothing else, so anyone already
        present when this client arrives has no nickname to show and renders as
        a short hash for as long as they stay quiet. A hub's ``/who`` reply is
        the only source of those names, and :meth:`learn_members` parses it.

        This is the one hub command the plugin sends on its own, a named
        exception to the rule that hub commands are the user's to type
        (``SPEC.md`` D15); ``/set plugins.var.python.rrc.who_on_join off``
        disables it. Nothing else is assumed about the hub: the request goes
        out as an ordinary message, and whatever comes back — a listing, an
        "unknown command" notice, an error, or silence — is rendered exactly as
        it would be had the user typed ``//who`` (``SPEC.md`` D17).

        The room is named in the text rather than left to the envelope's room
        field. ``rrcd`` accepts ``/who [room]`` and falls back to the envelope
        when the argument is absent, but naming it is unambiguous on a hub that
        does not.
        """
        if weechat.config_get_plugin("who_on_join") == "off":
            return
        self.say(room, f"/who {room}")

    def direct(self, target: str, text: str) -> None:
        """Send a direct message to *target*'s identity hash."""
        self.send({"op": "direct", "target": target, "text": text})

    def dm_buffer(self, identity: str) -> str:
        """Return the private buffer for *identity*, creating it if needed.

        Buffers are keyed by identity hash rather than nickname, because a
        nickname is advisory and may change or collide mid-conversation.
        """
        if identity in self.dms:
            return self.dms[identity]
        name = f"{SCRIPT_NAME}.{self.name}.{short(identity)}"
        existing = weechat.buffer_search("python", name)
        if existing:
            self.dms[identity] = existing
            self.prepare(existing, RESTRICT_PRIVATE)
            return existing
        pointer = weechat.buffer_new(
            f"{SCRIPT_NAME}.{self.name}.{short(identity)}",
            "rrc_input_cb",
            f"{self.name}/{DM_PREFIX}{identity}",
            "rrc_close_cb",
            f"{self.name}/{DM_PREFIX}{identity}",
        )
        weechat.buffer_set(pointer, "short_name", self.label(identity))
        weechat.buffer_set(pointer, "localvar_set_type", "private")
        weechat.buffer_set(pointer, "localvar_set_server", self.name)
        weechat.buffer_set(pointer, "title", f"direct messages with {identity}")
        self.prepare(pointer, RESTRICT_PRIVATE)
        self.dms[identity] = pointer
        return pointer

    def label(self, identity: str) -> str:
        """Return the best display name known for *identity*."""
        for members in self.members.values():
            nick = members.get(identity)
            if nick:
                return nick
        return short(identity)

    def learn_members(self, body: str) -> bool:
        """Adopt nicknames from a hub notice that lists a room's members.

        A hub answering ``//who`` names everyone at once, which is the only way
        to put nicknames on members who joined before this client did: the
        ``JOINED`` member list carries identity hashes and nothing else.

        This recognises a reply shape rather than assuming any hub command
        exists; a notice that does not match leaves everything untouched. Only
        members already known in the room are updated, so a notice cannot
        invent nicklist entries.

        Args:
            body: The notice text.

        Returns:
            ``True`` if the notice was a member listing.
        """
        listing = MEMBER_LIST.match(body.strip())
        if listing is None:
            return False
        room = listing.group(1).lower()
        known = self.members.get(room)
        if not known:
            return True
        rest, position = listing.group(2), 0
        for entry in MEMBER_ENTRY.finditer(rest):
            name = rest[position : entry.start()].strip().strip(",").strip()
            position = entry.end()
            prefix = entry.group(1)
            matches = [i for i in known if i.startswith(prefix)]
            if name and len(matches) == 1:
                known[matches[0]] = name
        self.refresh_nicklist(room)
        return True

    def note_member(self, room: str, identity: str, nick: str = "") -> None:
        """Record that *identity* is in *room*, keeping any known nickname.

        The hub itself is skipped. Hubs send room notices under their own
        identity, and listing a hub as a member of the rooms it relays would be
        wrong as well as confusing.
        """
        if not identity or identity == self.hub_identity:
            return
        members = self.members.setdefault(room, {})
        members[identity] = nick or members.get(identity, "")
        self.refresh_nicklist(room)

    def drop_member(self, room: str, identity: str) -> None:
        """Forget that *identity* is in *room*."""
        self.members.get(room, {}).pop(identity, None)
        self.refresh_nicklist(room)

    def refresh_nicklist(self, room: str) -> None:
        """Rebuild *room*'s nicklist from the members currently known."""
        pointer = self.rooms.get(room)
        if pointer is None:
            return
        weechat.nicklist_remove_all(pointer)
        for identity, nick in sorted(
            self.members.get(room, {}).items(), key=lambda item: item[1] or item[0]
        ):
            weechat.nicklist_add_nick(
                pointer,
                "",
                nick or short(identity),
                weechat.info_get("nick_color_name", identity),
                "",
                "bar_fg",
                1,
            )

    def resolve(self, token: str) -> str | None:
        """Return the identity hash *token* refers to, or ``None``.

        Accepts a full hash, a unique hash prefix, or a nickname seen in any
        room. Ambiguous input returns ``None`` rather than guessing, because
        guessing would send a private message to the wrong person.
        """
        candidate = token.strip().lower()
        known = {i for members in self.members.values() for i in members}
        known.update(self.dms)
        if len(candidate) == 32:
            return candidate
        by_nick = {
            identity
            for members in self.members.values()
            for identity, nick in members.items()
            if nick.lower() == candidate
        }
        by_prefix = {i for i in known if i.startswith(candidate)}
        matches = by_nick or by_prefix
        if len(matches) == 1:
            return matches.pop()
        if len(matches) > 1:
            self.display(f"{token} is ambiguous; use a full identity hash", "=!=")
        else:
            self.display(f"no one here is called {token}", "=!=")
        return None

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
        # Helper-sourced, so not hostile — but it is now a colour key as well
        # as display text, and every other displayed value is cleaned. Being
        # the one exception is not worth the reader's second look.
        self.identity = clean(event.get("hash"))
        # The identity arrives after the hub buffer is already open, which is
        # why seeding cannot be a creation-time job only (SPEC.md D24).
        self.publish_mentions()
        self.display(f"your identity is {self.identity}", "--")

    def _ev_state(self, event: dict) -> None:
        """Report a connection state change."""
        self.state = event.get("state", "")
        reason = event.get("reason")
        self.display(f"{self.state}" + (f" ({clean(reason)})" if reason else ""), "--")

    def _ev_welcome(self, event: dict) -> None:
        """Show the hub's greeting line once the session is open."""
        self.hub_identity = clean(event.get("src"))
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
        self.members[room] = {}
        for identity in members:
            self.note_member(room, clean(identity))
        show(buffer, f"--\tjoined {room} ({len(members)} present)", TAG_JOIN)
        # Asked after the join line so the hub's answer reads as a reply to it.
        # This runs on a re-JOIN after an outage too, which is when the
        # nicklist has just been rebuilt from hashes and needs it most.
        self.who(room)

    def _ev_parted(self, event: dict) -> None:
        """Report that we left a room."""
        room = clean(event.get("room"))
        if room in self.rooms:
            show(self.rooms[room], f"--\tleft {room}", TAG_PART)
        self.members.pop(room, None)

    def _ev_join(self, event: dict) -> None:
        """Report somebody else arriving in a room."""
        room = clean(event.get("room"))
        for member in event.get("members") or []:
            identity = clean(member)
            nick = clean(event.get("nick"))
            # A hub may announce the same arrival more than once, for instance
            # when a client reconnects quickly. Announcing somebody already in
            # the nicklist is noise, so update what is known and stay quiet.
            already_present = identity in self.members.get(room, {})
            self.room_buffer(room)
            self.note_member(room, identity, nick)
            if already_present:
                continue
            show(
                self.room_buffer(room),
                f"-->\t{coloured(identity, nick or short(identity))} joined {room}",
                TAG_JOIN,
                src=identity,
                name=nick or short(identity),
            )

    def _ev_part(self, event: dict) -> None:
        """Report somebody else leaving a room."""
        room = clean(event.get("room"))
        if room not in self.rooms:
            return
        for member in event.get("members") or []:
            identity = clean(member)
            # Likewise, only announce a departure for somebody we still list.
            if identity not in self.members.get(room, {}):
                continue
            who = clean(event.get("nick")) or short(identity)
            name = coloured(identity, who)
            show(
                self.rooms[room],
                f"<--\t{name} left {room}",
                TAG_PART,
                src=identity,
                name=who,
            )
            self.drop_member(room, identity)

    def _ev_chat(self, event: dict) -> None:
        """Render room content, or a hub-wide notice with no room."""
        room = event.get("room")
        if room:
            self.room_buffer(clean(room))
            self.note_member(
                clean(room), clean(event.get("src")), clean(event.get("nick"))
            )
        target = self.room_buffer(clean(room)) if room else self.buffer
        body = clean(event.get("body"))
        src = clean(event.get("src"))
        who = speaker(event)
        name = coloured(src, who)
        kind = event.get("kind")
        # A /who reply names everybody in the room, so it carries the user's own
        # nickname and short hash by construction -- and D15 makes one arrive
        # after every join and every rejoin. A roster is not somebody addressing
        # you, so it is exempted below. Nothing is correlated with the request
        # that provoked it: this recognises a line by its own shape, which
        # learn_members already does to harvest the names (SPEC.md D25).
        roster = kind == "notice" and self.learn_members(body)
        if src and src == self.identity:
            # The hub returns every message to its author, so the user's own
            # words arrive as ordinary inbound traffic. Verified against WeeChat
            # 4.10: neither the buffer's nick local variable nor the self_msg
            # tag suppresses a self-highlight, so both are said explicitly.
            level: tuple[str, ...] = SELF_TAGS
        else:
            # A notice with no room lands on the server buffer, which is where
            # MOTDs and member listings arrive: hub chatter is activity, not a
            # message. It can still reach highlight level if it names the user,
            # and WeeChat decides that, not this.
            level = () if kind == "notice" and not room else (NOTIFY_MESSAGE,)
            if roster:
                level += (NO_HIGHLIGHT,)
        if kind == "action":
            show(target, f" *\t{name} {body}", TAG_ACTION, *level, src=src, name=who)
        elif kind == "notice":
            show(
                target,
                f"--\t{body}" if not room else f"--\t{name}: {body}",
                TAG_NOTICE,
                *level,
                src=src,
                name=who,
            )
        else:
            show(target, f"{name}\t{body}", TAG_MSG, *level, src=src, name=who)

    def _ev_direct(self, event: dict) -> None:
        """Show a direct message in a private buffer for its sender."""
        identity = clean(event.get("src"))
        buffer = self.dm_buffer(identity)
        nick = clean(event.get("nick"))
        if nick:
            weechat.buffer_set(buffer, "short_name", nick)
        show(
            buffer,
            f"{coloured(identity, nick or short(identity))}"
            f"\t{clean(event.get('body'))}",
            TAG_DIRECT,
            NOTIFY_PRIVATE,
            src=identity,
            name=nick or short(identity),
        )

    def _ev_pong(self, event: dict) -> None:
        """Record the measured round-trip time."""
        self.lag = f"{event.get('lag_ms')}ms"
        self.display(f"lag is {self.lag}", "--")

    def _ev_nick(self, event: dict) -> None:
        """Record a confirmed nickname change."""
        self.nick = clean(event.get("nick"))
        self.publish_mentions()
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
