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
"""IRC verb interception and hub-command passthrough (ACCEPTANCE C2, C3).

These hooks fire for every ``/join`` typed anywhere in WeeChat, so the decisive
test is not that they work in an RRC buffer but that they stand aside
everywhere else. A regression here would silently break the irc plugin.
"""

from __future__ import annotations

import pytest

PEER = "1f5a80f61a6194267cf6b6df6a954adb"
OTHER = "aabbccddeeff00112233445566778899"


@pytest.fixture
def room(connected):
    """Return the fixtures plus a joined ``#general`` room buffer."""
    weechat, rrc, connection, process = connected
    buffer = connection.room_buffer("#general")
    connection.note_member("#general", PEER, "bob")
    return weechat, rrc, connection, process, buffer


# -- interception happens only in this script's buffers (C2) ---------------


@pytest.mark.parametrize(
    "command",
    ["/join #x", "/part", "/me waves", "/msg bob hi", "/query bob", "/nick bob"],
)
def test_verbs_pass_through_in_foreign_buffers(room, command):
    """Every intercepted verb is left alone outside this script's buffers."""
    weechat, rrc, connection, process, buffer = room
    other = weechat.buffer_new("irc.libera.#weechat", "", "", "", "")
    before = len(process.written)
    result = rrc.rrc_run_cb("", other, command)
    assert result == weechat.WEECHAT_RC_OK, "must not eat another plugin's command"
    assert len(process.written) == before, "must not act on a foreign buffer"


@pytest.mark.parametrize(
    "command",
    ["/join #x", "/part", "/me waves", "/msg bob hi", "/query bob", "/nick bob"],
)
def test_verbs_are_consumed_in_rrc_buffers(room, command):
    """Inside an RRC buffer the verb is handled and not passed on."""
    weechat, rrc, connection, process, buffer = room
    assert rrc.rrc_run_cb("", buffer, command) == weechat.WEECHAT_RC_OK_EAT


def test_every_intercepted_verb_is_hooked(wee):
    """Each documented verb has a command_run hook installed."""
    weechat, rrc = wee
    rrc.main()
    hooked = {
        h["command"] for h in weechat.state.hooks.values() if h["kind"] == "command_run"
    }
    assert hooked == {f"/{verb}" for verb in rrc.INTERCEPTED}
    assert hooked == {"/join", "/part", "/me", "/msg", "/query", "/nick"}


# -- the verbs themselves --------------------------------------------------


def test_join_enters_a_room(room):
    """``/join`` sends a join for the named room."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/join #radio")
    assert process.written[-1] == {"op": "join", "room": "#radio"}


def test_part_defaults_to_the_current_room(room):
    """``/part`` with no argument leaves the room you are in."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/part")
    assert process.written[-1] == {"op": "part", "room": "#general"}


def test_part_accepts_an_explicit_room(room):
    """``/part #other`` leaves a room you are not currently viewing."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/part #other")
    assert process.written[-1] == {"op": "part", "room": "#other"}


def test_me_sends_an_action(room):
    """``/me`` produces an ACTION, not a message containing markup."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/me waves at everyone")
    assert process.written[-1] == {
        "op": "say",
        "room": "#general",
        "text": "waves at everyone",
        "kind": "action",
    }


def test_msg_resolves_a_nickname(room):
    """``/msg`` accepts a nickname seen in a room."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/msg bob hello there")
    assert process.written[-1] == {
        "op": "direct",
        "target": PEER,
        "text": "hello there",
    }


def test_msg_accepts_a_full_hash(room):
    """A full identity hash needs no lookup."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, f"/msg {OTHER} hi")
    assert process.written[-1]["target"] == OTHER


def test_msg_accepts_a_unique_hash_prefix(room):
    """A prefix is enough when it identifies exactly one person."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/msg 1f5a hi")
    assert process.written[-1]["target"] == PEER


def test_msg_refuses_an_ambiguous_prefix(room):
    """An ambiguous prefix is refused rather than sent to the wrong person."""
    weechat, rrc, connection, process, buffer = room
    connection.note_member("#general", "1f5aFFFFFFFFFFFFFFFFFFFFFFFFFFFF", "carol")
    before = len(process.written)
    rrc.rrc_run_cb("", buffer, "/msg 1f5a hi")
    assert len(process.written) == before
    assert any(
        "ambiguous" in line for line in weechat.state.buffers[connection.buffer].text
    )


def test_msg_reports_an_unknown_recipient(room):
    """A name nobody answers to is reported, not silently dropped."""
    weechat, rrc, connection, process, buffer = room
    before = len(process.written)
    rrc.rrc_run_cb("", buffer, "/msg nobody hi")
    assert len(process.written) == before
    assert any(
        "no one here is called" in line
        for line in weechat.state.buffers[connection.buffer].text
    )


def test_query_opens_a_private_buffer(room):
    """``/query`` opens the conversation without sending anything."""
    weechat, rrc, connection, process, buffer = room
    before = len(process.written)
    rrc.rrc_run_cb("", buffer, "/query bob")
    assert PEER in connection.dms
    assert len(process.written) == before


def test_query_with_text_also_sends(room):
    """``/query bob hi`` opens the buffer and sends the message."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/query bob hi there")
    assert PEER in connection.dms
    assert process.written[-1]["text"] == "hi there"


def test_query_for_an_unknown_person_opens_nothing(room):
    """An unresolvable target leaves no stray buffer behind."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/query nobody")
    assert connection.dms == {}


def test_nick_changes_the_advisory_label(room):
    """``/nick`` asks the helper to change the nickname."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, "/nick newname")
    assert process.written[-1] == {"op": "nick", "nick": "newname"}


@pytest.mark.parametrize(
    "command,expected",
    [
        ("/join", "usage: /join"),
        ("/msg", "usage: /msg"),
        ("/msg bob", "usage: /msg"),
        ("/query", "usage: /query"),
        ("/nick", "usage: /nick"),
    ],
)
def test_verbs_explain_their_usage(room, command, expected):
    """A verb given too little to work with explains what it needs."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_run_cb("", buffer, command)
    assert any(
        expected in line for line in weechat.state.buffers[connection.buffer].text
    )


def test_me_outside_a_room_is_refused(connected):
    """``/me`` on the hub buffer has no room to act in."""
    weechat, rrc, connection, process = connected
    rrc.rrc_run_cb("", connection.buffer, "/me waves")
    assert any(
        "/me works in a room" in line
        for line in weechat.state.buffers[connection.buffer].text
    )


def test_part_outside_a_room_is_refused(connected):
    """``/part`` on the hub buffer must name a room."""
    weechat, rrc, connection, process = connected
    rrc.rrc_run_cb("", connection.buffer, "/part")
    assert any(
        "usage: /part" in line for line in weechat.state.buffers[connection.buffer].text
    )


def test_verbs_work_from_a_private_buffer(room):
    """A private buffer belongs to the script, so its verbs are handled."""
    weechat, rrc, connection, process, buffer = room
    dm = connection.dm_buffer(PEER)
    assert rrc.rrc_run_cb("", dm, "/nick x") == weechat.WEECHAT_RC_OK_EAT


def test_me_in_a_private_buffer_is_refused(room):
    """A private buffer is not a room, so ACTION has nowhere to go."""
    weechat, rrc, connection, process, buffer = room
    dm = connection.dm_buffer(PEER)
    rrc.rrc_run_cb("", dm, "/me waves")
    assert any(
        "/me works in a room" in line
        for line in weechat.state.buffers[connection.buffer].text
    )


# -- hub-command passthrough (C3) ------------------------------------------


def test_a_doubled_slash_sends_a_hub_command(room):
    """``//who`` sends the literal ``/who``, which the hub parses.

    WeeChat hands the input callback the text with both slashes intact, so one
    is stripped here. This is how hub commands reach the hub without this
    script keeping a list of them, which EX1 warns against.
    """
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_input_cb("28c7c1a6/#general", buffer, "//who #general")
    assert process.written[-1] == {
        "op": "say",
        "room": "#general",
        "text": "/who #general",
    }


def test_ordinary_text_is_unaffected(room):
    """A normal message is sent verbatim."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_input_cb("28c7c1a6/#general", buffer, "hello everyone")
    assert process.written[-1]["text"] == "hello everyone"


def test_text_containing_a_slash_is_unaffected(room):
    """A slash mid-message is not an escape and is left alone."""
    weechat, rrc, connection, process, buffer = room
    rrc.rrc_input_cb("28c7c1a6/#general", buffer, "and/or maybe")
    assert process.written[-1]["text"] == "and/or maybe"


def test_typing_in_a_private_buffer_sends_a_direct_message(room):
    """Input in a private buffer becomes a direct message, echoed locally."""
    weechat, rrc, connection, process, buffer = room
    dm = connection.dm_buffer(PEER)
    rrc.rrc_input_cb(f"28c7c1a6/@{PEER}", dm, "hello privately")
    assert process.written[-1] == {
        "op": "direct",
        "target": PEER,
        "text": "hello privately",
    }
    # The hub does not echo direct messages, so this one is shown locally.
    assert len(weechat.state.buffers[dm].lines) == 1


def test_closing_a_private_buffer_does_not_part_a_room(room):
    """Closing a conversation is local; there is no room to leave."""
    weechat, rrc, connection, process, buffer = room
    connection.dm_buffer(PEER)
    before = len(process.written)
    rrc.rrc_close_cb(f"28c7c1a6/@{PEER}", "0x0")
    assert connection.dms == {}
    assert len(process.written) == before


def test_private_buffer_lookup_scans_every_conversation(room):
    """Buffer lookup scans past other conversations to find the right one."""
    weechat, rrc, connection, process, buffer = room
    connection.dm_buffer(PEER)
    second = connection.dm_buffer(OTHER)
    found, target = rrc.find_connection(second)
    assert found is connection
    assert target == rrc.DM_PREFIX + OTHER
