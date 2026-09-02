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
"""Event rendering and buffer handling (ACCEPTANCE C8).

Events are pushed through a real pipe so the non-blocking read path is
exercised, not mocked. Rendering tests concentrate on the cases where hostile
or missing data reaches a buffer.
"""

from __future__ import annotations

import os

import pytest

PEER = "1f5a80f61a6194267cf6b6df6a954adb"

#: Control characters, built rather than written, so this source file stays
#: free of them.
NUL, TAB, ESC = chr(0), chr(9), chr(27)


def deliver(rrc, connection, process, *events):
    """Push *events* down the helper pipe and let the script read them."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, str(process.stdout.fileno()))


def lines(weechat, buffer):
    """Return the text of every line printed to *buffer*."""
    return weechat.state.buffers[buffer].text


def raw_lines(weechat, buffer):
    """Return the untouched lines, including their prefix separators."""
    return weechat.state.buffers[buffer].lines


# -- the pipe --------------------------------------------------------------


def test_events_arrive_through_the_pipe(connected):
    """A complete frame on stdout is decoded and rendered."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": "abc123"})
    assert connection.identity == "abc123"
    assert any("abc123" in line for line in lines(weechat, connection.buffer))


def test_partial_frames_are_reassembled(connected):
    """A frame split across reads is buffered until its newline arrives."""
    weechat, rrc, connection, process = connected
    os.write(process._out_w, b'{"op":"identity","ha')
    rrc.rrc_stdout_cb(connection.name, "0")
    assert connection.identity == ""
    os.write(process._out_w, b'sh":"abc123"}\n')
    rrc.rrc_stdout_cb(connection.name, "0")
    assert connection.identity == "abc123"


def test_malformed_frames_are_dropped(connected):
    """Garbage on the pipe cannot break the WeeChat callback."""
    weechat, rrc, connection, process = connected
    os.write(process._out_w, b'not json\n[1,2,3]\n\n{"op":"identity","hash":"ok"}\n')
    rrc.rrc_stdout_cb(connection.name, "0")
    assert connection.identity == "ok"


def test_stdout_callback_without_a_process_is_safe(connected):
    """A callback firing after teardown returns cleanly."""
    weechat, rrc, connection, process = connected
    connection.process = None
    assert rrc.rrc_stdout_cb(connection.name, "0") == weechat.WEECHAT_RC_OK
    assert rrc.rrc_stdout_cb("nonexistent", "0") == weechat.WEECHAT_RC_OK


def test_stderr_is_surfaced_on_the_core_buffer(connected):
    """Helper diagnostics reach the user rather than vanishing."""
    weechat, rrc, connection, process = connected
    process.emit_stderr("RNS: something happened\n\n")
    rrc.rrc_stderr_cb(connection.name, "0")
    assert any("something happened" in line for line in weechat.state.core)


def test_stderr_callback_without_a_process_is_safe(connected):
    """The stderr callback tolerates a torn-down connection."""
    weechat, rrc, connection, process = connected
    connection.process = None
    assert rrc.rrc_stderr_cb(connection.name, "0") == weechat.WEECHAT_RC_OK
    assert rrc.rrc_stderr_cb("nonexistent", "0") == weechat.WEECHAT_RC_OK


def test_reading_a_closed_stream_returns_nothing(connected):
    """A closed descriptor yields no data instead of raising."""
    weechat, rrc, connection, process = connected
    process.stdout.close()
    assert rrc._read_available(process.stdout) == b""


# -- session events --------------------------------------------------------


def test_welcome_names_the_hub_and_sets_the_title(connected):
    """The hub's name and version are shown and become the buffer title."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "welcome", "hub": "SmokeTestHub", "version": "0.3.2"},
    )
    assert connection.hub_name == "SmokeTestHub"
    assert any("SmokeTestHub" in line for line in lines(weechat, connection.buffer))
    title = weechat.state.buffers[connection.buffer].properties["title"]
    assert "SmokeTestHub" in title


def test_welcome_without_a_name_still_renders(connected):
    """A hub that advertises nothing still produces a sensible line."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "welcome"})
    assert connection.hub_name == "unnamed hub"


def test_state_and_reconnect_are_reported(connected):
    """Connection state changes and retry delays are visible."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "state", "state": "up"},
        {"op": "state", "state": "down", "reason": "link timed out"},
        {"op": "reconnect", "seconds": 20.0},
    )
    text = "\n".join(lines(weechat, connection.buffer))
    assert "up" in text and "link timed out" in text and "20.0s" in text
    assert connection.state == "down"


def test_errors_are_reported(connected):
    """Errors from the helper or the hub are shown to the user."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "error", "message": "Rate limited."})
    assert any("Rate limited." in line for line in lines(weechat, connection.buffer))


def test_error_without_a_message_still_renders(connected):
    """A malformed error event does not produce a blank line."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "error"})
    assert any("unknown error" in line for line in lines(weechat, connection.buffer))


def test_pong_records_lag(connected):
    """A measured round trip is stored and displayed."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "pong", "lag_ms": 5})
    assert connection.lag == "5ms"


def test_nick_change_is_recorded(connected):
    """A confirmed nickname change updates local state."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "nick", "nick": "newnick"})
    assert connection.nick == "newnick"


def test_unknown_events_are_ignored(connected):
    """An event from a newer helper is dropped, not raised."""
    weechat, rrc, connection, process = connected
    before = len(lines(weechat, connection.buffer))
    deliver(rrc, connection, process, {"op": "from_the_future"}, {})
    assert len(lines(weechat, connection.buffer)) == before


# -- rooms -----------------------------------------------------------------


def test_joining_opens_a_room_buffer(connected):
    """Our own join confirmation creates the room buffer."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [PEER]},
    )
    buffer = weechat.state.buffer("rrc.28c7c1a6.#general")
    assert buffer is not None
    assert buffer.properties["localvar_set_channel"] == "#general"
    assert any("joined #general" in line for line in buffer.text)


def test_room_buffers_are_reused(connected):
    """A second event for the same room does not open a second buffer."""
    weechat, rrc, connection, process = connected
    first = connection.room_buffer("#general")
    assert connection.room_buffer("#general") == first
    assert len(connection.rooms) == 1


def test_others_joining_and_leaving_are_shown(connected):
    """Arrivals and departures are reported in the room buffer."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "join", "room": "#general", "members": [PEER], "nick": "bob"},
        {"op": "part", "room": "#general", "members": [PEER], "nick": "bob"},
    )
    text = "\n".join(lines(weechat, connection.rooms["#general"]))
    assert "bob joined #general" in text
    assert "bob left #general" in text


def test_a_member_without_a_nickname_shows_a_short_hash(connected):
    """Nicknames are advisory, so the identity hash is the fallback."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "join", "room": "#general", "members": [PEER]},
    )
    assert PEER[:8] in "\n".join(lines(weechat, connection.rooms["#general"]))


def test_parting_an_unknown_room_is_ignored(connected):
    """Events for a room with no buffer do not create one."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "parted", "room": "#never"},
        {"op": "part", "room": "#never", "members": [PEER]},
    )
    assert weechat.state.buffer("rrc.28c7c1a6.#never") is None


def test_own_part_is_reported(connected):
    """Leaving a room is confirmed in that room's buffer."""
    weechat, rrc, connection, process = connected
    connection.room_buffer("#general")
    deliver(rrc, connection, process, {"op": "parted", "room": "#general"})
    assert "left #general" in "\n".join(lines(weechat, connection.rooms["#general"]))


# -- chat rendering --------------------------------------------------------


@pytest.mark.parametrize(
    "kind,body,expected",
    [
        ("msg", "hello", "bob" + TAB + "hello"),
        ("action", "waves", " *" + TAB + "bob waves"),
        ("notice", "fyi", "--" + TAB + "bob: fyi"),
    ],
)
def test_each_chat_kind_renders_distinctly(connected, kind, body, expected):
    """A message, an emote and a notice are visually distinguishable."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {
            "op": "chat",
            "kind": kind,
            "room": "#general",
            "src": PEER,
            "nick": "bob",
            "body": body,
        },
    )
    assert expected in raw_lines(weechat, connection.rooms["#general"])


def test_a_roomless_notice_lands_on_the_server_buffer(connected):
    """A hub-wide greeting has no room and belongs on the hub buffer."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "chat", "kind": "notice", "src": PEER, "body": "Welcome!", "room": None},
    )
    assert any("Welcome!" in line for line in lines(weechat, connection.buffer))


def test_a_speaker_without_a_nickname_shows_a_short_hash(connected):
    """Rendering falls back to the authoritative identity hash."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "chat", "kind": "msg", "room": "#general", "src": PEER, "body": "hi"},
    )
    expected = PEER[:8] + TAB + "hi"
    assert expected in raw_lines(weechat, connection.rooms["#general"])


def test_control_characters_cannot_forge_a_buffer_line(connected):
    """Hub-supplied text is stripped of anything that could fake a line.

    A nickname carrying a tab could otherwise invent a prefix separator, and an
    escape sequence could recolour the display.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {
            "op": "chat",
            "kind": "msg",
            "room": "#general",
            "src": PEER,
            "nick": "e" + NUL + "vil" + TAB + "admin",
            "body": "one" + ESC + "[31mtwo",
        },
    )
    rendered = raw_lines(weechat, connection.rooms["#general"])
    assert len(rendered) == 1
    assert NUL not in rendered[0]
    assert ESC not in rendered[0]
    assert rendered[0].count(TAB) == 1


def test_a_direct_message_opens_a_private_buffer(connected):
    """A direct message gets its own buffer, keyed by identity hash."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "direct", "src": PEER, "nick": "alice", "body": "psst"},
    )
    buffer = weechat.state.buffer(f"rrc.28c7c1a6.{PEER[:8]}")
    assert buffer is not None
    assert buffer.properties["localvar_set_type"] == "private"
    assert buffer.properties["short_name"] == "alice"
    assert "alice" + TAB + "psst" in buffer.lines


def test_direct_messages_reuse_one_buffer_per_peer(connected):
    """A conversation stays in one buffer even as the nickname changes."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "direct", "src": PEER, "nick": "alice", "body": "one"},
        {"op": "direct", "src": PEER, "nick": "alice2", "body": "two"},
    )
    assert len(connection.dms) == 1
    buffer = weechat.state.buffers[connection.dms[PEER]]
    assert len(buffer.lines) == 2
    assert buffer.properties["short_name"] == "alice2"


def test_a_direct_message_without_a_nickname_uses_the_hash(connected):
    """With no nickname, the private buffer is labelled by identity."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "direct", "src": PEER, "body": "hi"})
    buffer = weechat.state.buffers[connection.dms[PEER]]
    assert PEER[:8] + TAB + "hi" in buffer.lines


# -- buffer input and closing ----------------------------------------------


def test_typing_in_a_room_sends_a_message(connected):
    """Buffer input goes to the room, with no local echo."""
    weechat, rrc, connection, process = connected
    room_buffer = connection.room_buffer("#general")
    rrc.rrc_input_cb("28c7c1a6/#general", room_buffer, "hello there")
    assert process.written[-1] == {
        "op": "say",
        "room": "#general",
        "text": "hello there",
    }
    # The hub echoes our own messages back, so nothing is printed locally.
    assert weechat.state.buffers[room_buffer].lines == []


def test_typing_in_the_server_buffer_is_explained(connected):
    """The hub buffer is not a room, and says so."""
    weechat, rrc, connection, process = connected
    rrc.rrc_input_cb("28c7c1a6", connection.buffer, "hello")
    assert "type in a room buffer" in "\n".join(lines(weechat, connection.buffer))


def test_input_for_an_unknown_connection_is_ignored(connected):
    """Input arriving after teardown is dropped cleanly."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_input_cb("gone/#x", "0x0", "hi") == weechat.WEECHAT_RC_OK


def test_closing_a_room_buffer_parts_the_room(connected):
    """Closing a room buffer leaves the room."""
    weechat, rrc, connection, process = connected
    connection.room_buffer("#general")
    rrc.rrc_close_cb("28c7c1a6/#general", "0x0")
    assert process.written[-1] == {"op": "part", "room": "#general"}
    assert "#general" not in connection.rooms


def test_closing_the_server_buffer_disconnects(connected):
    """Closing the hub buffer ends the session entirely."""
    weechat, rrc, connection, process = connected
    rrc.rrc_close_cb("28c7c1a6", "0x0")
    assert rrc.connections == {}
    assert process.closed


def test_closing_an_unknown_connection_is_ignored(connected):
    """A close callback for a gone connection returns cleanly."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_close_cb("gone", "0x0") == weechat.WEECHAT_RC_OK


def test_room_buffers_survive_a_link_outage(connected):
    """Buffers and scrollback persist across a reconnect (ACCEPTANCE C5).

    RRC replays nothing, so anything already on screen is all the user has;
    tearing the buffer down on a dropped Link would discard it.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {
            "op": "chat",
            "kind": "msg",
            "room": "#general",
            "src": PEER,
            "nick": "bob",
            "body": "said before the outage",
        },
    )
    buffer = connection.rooms["#general"]
    before = list(weechat.state.buffers[buffer].lines)

    deliver(
        rrc,
        connection,
        process,
        {"op": "state", "state": "down", "reason": "link timed out"},
        {"op": "reconnect", "seconds": 10.0},
        {"op": "state", "state": "up"},
    )
    assert connection.rooms.get("#general") == buffer, "the room buffer was closed"
    assert weechat.state.buffers[buffer].lines[: len(before)] == before


@pytest.mark.parametrize(
    "hostile",
    [
        "line one" + chr(10) + "forged second line",
        "colour " + chr(25) + "F31 code",
        "attribute " + chr(26) + "*bold",
        "reset " + chr(28) + " here",
    ],
)
def test_newlines_and_weechat_colour_codes_are_stripped(connected, hostile):
    """Hub text cannot inject newlines or WeeChat's own formatting bytes.

    WeeChat reads 0x19, 0x1A, 0x1B and 0x1C as colour and attribute markers, so
    a hub could otherwise recolour or restyle a line it did not own.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {
            "op": "chat",
            "kind": "msg",
            "room": "#general",
            "src": PEER,
            "nick": "bob",
            "body": hostile,
        },
    )
    rendered = raw_lines(weechat, connection.rooms["#general"])
    assert len(rendered) == 1, "a newline forged an extra buffer line"
    for code in (10, 25, 26, 27, 28):
        assert chr(code) not in rendered[0]
