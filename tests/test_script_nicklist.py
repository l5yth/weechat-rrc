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
"""Nicklist maintenance (ACCEPTANCE C6).

Membership is ephemeral and nicknames are advisory, so the nicklist has to cope
with members who have no name yet, names that arrive later, and a room that
evaporates when the Link drops.
"""

from __future__ import annotations

import pytest

ALICE = "1f5a80f61a6194267cf6b6df6a954adb"
BOB = "aabbccddeeff00112233445566778899"


def nicks(weechat, connection, room):
    """Return the nicknames currently shown in *room*'s nicklist."""
    return set(weechat.state.buffers[connection.rooms[room]].nicks)


def deliver(rrc, connection, process, *events):
    """Push *events* through the helper pipe."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, "0")


def test_joining_seeds_the_nicklist_from_the_member_list(connected):
    """Our own JOINED carries everyone present, so the list starts complete."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE, BOB]},
    )
    assert nicks(weechat, connection, "#general") == {ALICE[:8], BOB[:8]}


def test_a_later_join_adds_exactly_one_nick(connected):
    """A single-member JOINED means one person arrived."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    assert nicks(weechat, connection, "#general") == {ALICE[:8], "bob"}


def test_a_part_removes_one_nick(connected):
    """PARTED removes only the member who left."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE, BOB]},
        {"op": "part", "room": "#general", "members": [BOB]},
    )
    assert nicks(weechat, connection, "#general") == {ALICE[:8]}


def test_a_member_with_no_nickname_shows_a_short_hash(connected):
    """The authoritative identity is shown when no nickname is known."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    assert nicks(weechat, connection, "#general") == {ALICE[:8]}
    assert "" not in nicks(weechat, connection, "#general")


def test_speaking_teaches_the_nicklist_a_nickname(connected):
    """A nickname learned from a message replaces the placeholder hash."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
        {
            "op": "chat",
            "kind": "msg",
            "room": "#general",
            "src": ALICE,
            "nick": "alice",
            "body": "hi",
        },
    )
    assert nicks(weechat, connection, "#general") == {"alice"}


def test_a_message_without_a_nickname_keeps_the_known_one(connected):
    """A later nameless message does not erase a nickname already learned."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
        {
            "op": "chat",
            "kind": "msg",
            "room": "#general",
            "src": ALICE,
            "nick": "alice",
            "body": "one",
        },
        {"op": "chat", "kind": "msg", "room": "#general", "src": ALICE, "body": "two"},
    )
    assert nicks(weechat, connection, "#general") == {"alice"}


def test_a_speaker_who_never_joined_is_added(connected):
    """Presence is a convenience, so a message is enough to list someone."""
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
            "src": BOB,
            "nick": "bob",
            "body": "hi",
        },
    )
    assert nicks(weechat, connection, "#general") == {"bob"}


def test_leaving_a_room_forgets_its_membership(connected):
    """Membership does not linger after we leave."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
        {"op": "parted", "room": "#general"},
    )
    assert "#general" not in connection.members


def test_rejoining_replaces_stale_membership(connected):
    """A fresh JOINED replaces the old list rather than merging into it."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE, BOB]},
    )
    connection._pending = None
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    assert nicks(weechat, connection, "#general") == {ALICE[:8]}


def test_membership_of_an_unopened_room_is_not_rendered(connected):
    """Updating a room with no buffer does not raise."""
    weechat, rrc, connection, process = connected
    connection.note_member("#never", ALICE, "alice")
    assert "#never" not in connection.rooms


def test_closing_a_room_buffer_forgets_its_members(connected):
    """Closing a room clears its membership along with its buffer."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    rrc.rrc_close_cb("28c7c1a6/#general", "0x0")
    assert "#general" not in connection.members


def test_label_prefers_a_known_nickname(connected):
    """Private buffers are labelled with a nickname when one is known."""
    weechat, rrc, connection, process = connected
    connection.note_member("#general", ALICE, "alice")
    assert connection.label(ALICE) == "alice"
    assert connection.label(BOB) == BOB[:8]


def test_a_nicklist_is_enabled_on_room_buffers(connected):
    """Room buffers ask WeeChat to show a nicklist."""
    weechat, rrc, connection, process = connected
    buffer = connection.room_buffer("#general")
    assert weechat.state.buffers[buffer].properties["nicklist"] == "1"


def test_the_hub_is_not_listed_as_a_room_member(connected):
    """Hub notices carry the hub's identity, which is not a member.

    Hubs send room notices under their own identity hash, so without this the
    hub would appear in the nicklist of every room it relays.
    """
    weechat, rrc, connection, process = connected
    hub = "2aee7b73bd6ee0eef791d1dc3b7d68ef"
    deliver(
        rrc,
        connection,
        process,
        {"op": "welcome", "src": hub, "hub": "SmokeTestHub"},
        {"op": "joined", "room": "#general", "members": [ALICE]},
        {
            "op": "chat",
            "kind": "notice",
            "room": "#general",
            "src": hub,
            "body": "room #general: unregistered",
        },
    )
    assert connection.hub_identity == hub
    assert nicks(weechat, connection, "#general") == {ALICE[:8]}


def test_a_member_with_no_identity_is_not_listed(connected):
    """A malformed member entry does not create a blank nicklist row."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    connection.note_member("#general", "")
    assert nicks(weechat, connection, "#general") == {ALICE[:8]}


def test_a_repeated_join_is_announced_once(connected):
    """A hub may announce the same arrival twice; the room shows it once.

    Observed against a public hub when a client reconnected within two
    seconds: the same "joined" line was rendered twice at the same timestamp.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    text = "\n".join(weechat.state.buffers[connection.rooms["#general"]].text)
    assert text.count("bob joined") == 1
    assert nicks(weechat, connection, "#general") == {"bob"}


def test_a_repeated_part_is_announced_once(connected):
    """A departure for somebody already gone is not announced again."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [BOB]},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    text = "\n".join(weechat.state.buffers[connection.rooms["#general"]].text)
    assert text.count("left") == 1


def test_a_rejoin_after_a_part_is_announced(connected):
    """Somebody who genuinely left and came back is announced again."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [BOB]},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    text = "\n".join(weechat.state.buffers[connection.rooms["#general"]].text)
    assert text.count("bob joined") == 1
    assert text.count("left") == 1
