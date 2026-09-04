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

import os

import weechat

# No ``sys.modules`` housekeeping is needed for ``/script reload``. WeeChat runs
# each Python script in its own sub-interpreter and builds a fresh one on every
# load, so ``rrc.ui.*`` is re-imported each time rather than served stale from a
# cache. Verified against WeeChat 4.10 by reloading a script whose sibling
# module records its own imports: the sibling was imported twice.
from rrc.ui import (
    DEFAULTS,
    DM_PREFIX,
    SCRIPT_AUTHOR,
    SCRIPT_DESC,
    SCRIPT_LICENSE,
    SCRIPT_NAME,
    SCRIPT_VERSION,
)
from rrc.ui import connection as connection_mod
from rrc.ui import interpreter, render
from rrc.ui.connection import Connection
from rrc.ui.render import SELF_TAGS, TAG_DIRECT, clean, coloured, log, short, show

#: IRC verbs this script handles inside its own buffers (SPEC.md D3).
INTERCEPTED = ("join", "part", "me", "msg", "query", "nick")

#: Live connections, keyed by the short hub name shown in buffer names.
connections: dict[str, "Connection"] = {}


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
        for identity, pointer in connection.dms.items():
            if buffer == pointer:
                return connection, DM_PREFIX + identity
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
    name, _, target = data.partition("/")
    connection = connections.get(name)
    if connection is None:
        return weechat.WEECHAT_RC_OK
    if not target:
        connection.display("this is the hub buffer; type in a room buffer", "=!=")
        return weechat.WEECHAT_RC_OK
    if target.startswith(DM_PREFIX):
        connection.direct(target[len(DM_PREFIX) :], text)
        # Your own echo is a fifth place a person is named, so it is coloured
        # like the other four (SPEC.md D19) — keyed on your identity, which is
        # what makes your name look the same here as it does to everyone else.
        show(
            connection.dms[target[len(DM_PREFIX) :]],
            f"{coloured(connection.identity, connection.nick or 'you')}\t{text}",
            TAG_DIRECT,
            *SELF_TAGS,
            src=connection.identity,
            name=connection.nick or "you",
        )
        return weechat.WEECHAT_RC_OK
    connection.say(target, text)
    return weechat.WEECHAT_RC_OK


def rrc_close_cb(data: str, buffer: str) -> int:
    """Part the room, or disconnect entirely, when a buffer is closed."""
    name, _, target = data.partition("/")
    connection = connections.get(name)
    if connection is None:
        return weechat.WEECHAT_RC_OK
    if target.startswith(DM_PREFIX):
        connection.dms.pop(target[len(DM_PREFIX) :], None)
    elif target:
        connection.rooms.pop(target, None)
        connection.members.pop(target, None)
        connection.send({"op": "part", "room": target})
    else:
        connection.stop()
        connections.pop(name, None)
    return weechat.WEECHAT_RC_OK


def rrc_run_cb(data: str, buffer: str, command: str) -> int:
    """Handle an IRC verb typed inside one of this script's buffers.

    Buffer ownership is checked with :func:`find_connection`, not the
    single-connection fallback: these hooks fire for every ``/join`` anywhere in
    WeeChat, so anything that is not ours must pass through untouched or the
    irc plugin would stop working.
    """
    connection, target = find_connection(buffer)
    if connection is None:
        return weechat.WEECHAT_RC_OK
    verb, _, rest = command.strip().partition(" ")
    handler = {
        "/join": _run_join,
        "/part": _run_part,
        "/me": _run_me,
        "/msg": _run_msg,
        "/query": _run_query,
        "/nick": _run_nick,
    }.get(verb.lower())
    if handler is None:  # pragma: no cover - only hooked verbs reach here
        return weechat.WEECHAT_RC_OK
    handler(connection, target, rest.strip())
    return weechat.WEECHAT_RC_OK_EAT


def _room_of(target: str) -> str:
    """Return *target* if it names a room, else an empty string."""
    return "" if target.startswith(DM_PREFIX) else target


def _run_join(connection: "Connection", target: str, rest: str) -> None:
    """``/join #room`` enters a room."""
    if not rest:
        connection.display("usage: /join <#room>", "=!=")
        return
    connection.send({"op": "join", "room": rest.split()[0]})


def _run_part(connection: "Connection", target: str, rest: str) -> None:
    """``/part [#room]`` leaves a room, defaulting to the current one."""
    room = rest.split()[0] if rest else _room_of(target)
    if not room:
        connection.display("usage: /part <#room>", "=!=")
        return
    connection.send({"op": "part", "room": room})


def _run_me(connection: "Connection", target: str, rest: str) -> None:
    """``/me does a thing`` sends an ACTION to the current room."""
    room = _room_of(target)
    if not room:
        connection.display("/me works in a room buffer", "=!=")
        return
    connection.send({"op": "say", "room": room, "text": rest, "kind": "action"})


def _run_msg(connection: "Connection", target: str, rest: str) -> None:
    """``/msg <target> <text>`` sends one direct message."""
    recipient, _, text = rest.partition(" ")
    if not recipient or not text.strip():
        connection.display("usage: /msg <nick|hash> <message>", "=!=")
        return
    identity = connection.resolve(recipient)
    if identity is not None:
        connection.direct(identity, text.strip())


def _run_query(connection: "Connection", target: str, rest: str) -> None:
    """``/query <target> [text]`` opens a private buffer, optionally sending."""
    recipient, _, text = rest.partition(" ")
    if not recipient:
        connection.display("usage: /query <nick|hash> [message]", "=!=")
        return
    identity = connection.resolve(recipient)
    if identity is None:
        return
    connection.dm_buffer(identity)
    if text.strip():
        connection.direct(identity, text.strip())


def _run_nick(connection: "Connection", target: str, rest: str) -> None:
    """``/nick <nickname>`` changes the advisory nickname."""
    if not rest:
        connection.display("usage: /nick <nickname>", "=!=")
        return
    connection.send({"op": "nick", "nick": rest.split()[0]})


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
        # Close the buffer too. Leaving it open made the next attempt fail
        # with "a buffer with same name already exists", burying the real
        # error under a second, unrelated one.
        connections.pop(name, None)
        weechat.buffer_close(connection.buffer)
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
        "Inside a room buffer the familiar IRC verbs work as well: /join,\n"
        "/part, /me, /msg, /query and /nick. They are left alone everywhere\n"
        "else, so the irc plugin is unaffected.\n\n"
        "To send a hub command such as /who or /topic, double the slash:\n"
        "typing //who sends the literal /who to the hub, which interprets it.\n"
        "Hub commands differ between hubs, so none are assumed here, with\n"
        "one exception: joining a room sends /who to put names on the members\n"
        "already there. Set who_on_join to off to stop that.\n\n"
        "A running Reticulum shared instance is assumed; this script never\n"
        "reads or writes your Reticulum configuration.",
        "connect || disconnect || list || status || join || part || nick || ping",
        "rrc_command_cb",
        "",
    )
    for verb in INTERCEPTED:
        weechat.hook_command_run(f"/{verb}", "rrc_run_cb", "")


if __name__ == "__main__":
    main()
