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
"""Nickname colours (ACCEPTANCE N1-N6, SPEC D19-D22).

Colour is keyed on the identity hash rather than the advisory nickname, so it
follows the person through a rename and an impostor taking a nickname does not
take the colour with it. WeeChat computes every colour; nothing here does.
"""

from __future__ import annotations

ALICE = "1f5a80f61a6194267cf6b6df6a954adb"
BOB = "aabbccddeeff00112233445566778899"

#: WeeChat's own formatting bytes: colour, attribute, escape, reset.
COLOUR, ATTRIBUTE, ESCAPE, RESET = "\x19", "\x1a", "\x1b", "\x1c"


# -- the oracle itself ---------------------------------------------------


def test_the_fake_is_realistic_enough_to_test_against(wee):
    """The stand-in must return real colour shapes, or nothing below tests.

    Both functions once returned "". Against that fake no colour code ever
    reaches a rendered line, so every assertion in this module would pass while
    proving nothing, and the C8 injection check would pass for the wrong
    reason. This guards the guard.
    """
    weechat, rrc = wee
    assert weechat.color("reset") == RESET
    first = weechat.info_get("nick_color", ALICE)
    second = weechat.info_get("nick_color", BOB)
    assert first.startswith(COLOUR), "a colour code must carry WeeChat's 0x19"
    assert second.startswith(COLOUR)
    assert first != second, "distinct identities must get distinct colours"
    assert weechat.info_get("nick_color", ALICE) == first, "colour must be stable"
    assert weechat.info_get("nick_color_name", ALICE) == first[1:]


def deliver(rrc, connection, process, *events):
    """Push *events* through the helper pipe and let the script read them."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, "0")


def room_lines(weechat, connection, room="#general"):
    """Return the raw lines printed to *room*, colour codes intact."""
    return weechat.state.buffers[connection.rooms[room]].lines


def say(rrc, connection, process, src, body="hi", nick=None, kind="msg"):
    """Deliver one room message from *src* and return the line it rendered."""
    event = {"op": "chat", "kind": kind, "room": "#general", "src": src, "body": body}
    if nick is not None:
        event["nick"] = nick
    deliver(rrc, connection, process, event)


# -- N1: every place a person is named ------------------------------------


def test_the_nicklist_surface_carries_the_identity_colour(connected):
    """The colour argument is WeeChat's answer, not the old literal bar_fg."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    entry = weechat.state.buffers[connection.rooms["#general"]].nicks[ALICE[:8]]
    assert entry["color"] == weechat.info_get("nick_color_name", ALICE)
    assert entry["color"] != "bar_fg"


def test_the_message_and_action_surfaces_colour_the_speaker(connected):
    """A MSG and an ACTION both name their speaker in that person's colour."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, "hi", nick="alice")
    say(rrc, connection, process, ALICE, "waves", nick="alice", kind="action")
    code = weechat.info_get("nick_color", ALICE)
    rendered = room_lines(weechat, connection)
    assert rendered[-2].startswith(code + "alice" + RESET)
    assert rendered[-1].startswith(" *\t" + code + "alice" + RESET)


def test_the_join_and_part_surfaces_colour_the_name(connected):
    """Arrival and departure lines colour the person they are about."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    wrapped = weechat.info_get("nick_color", BOB) + "bob" + RESET
    rendered = room_lines(weechat, connection)
    assert f"-->\t{wrapped} joined #general" in rendered
    assert f"<--\t{wrapped} left #general" in rendered


def test_the_private_buffer_surface_colours_the_sender(connected):
    """A direct message names its sender in that sender's colour."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "direct", "src": ALICE, "nick": "a", "body": "p"},
    )
    line = weechat.state.buffers[connection.dms[ALICE]].lines[-1]
    assert line == weechat.info_get("nick_color", ALICE) + "a" + RESET + "\t" + "p"


# -- N2: WeeChat computes it, keyed on the full hash ----------------------


def test_weechat_computes_it_from_the_full_hash_not_the_short_one(connected):
    """The key is the 32-hex identity, not the 8-hex form and not the nick.

    Real WeeChat maps the full and short forms of one hash to different
    colours, so the choice has to be pinned rather than left to whichever
    string happens to be at hand (SPEC D20).
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, nick="bob")
    line = room_lines(weechat, connection)[-1]
    assert line.startswith(weechat.info_get("nick_color", ALICE))
    assert not line.startswith(weechat.info_get("nick_color", ALICE[:8]))
    assert not line.startswith(weechat.info_get("nick_color", "bob"))


def test_weechat_computes_the_value_and_it_is_used_verbatim(connected):
    """Whatever info_get returns is emitted unchanged, never post-processed."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, "hi", nick="alice")
    code = weechat.info_get("nick_color", ALICE)
    assert room_lines(weechat, connection)[-1] == code + "alice" + RESET + "\thi"


# -- N3: the colour follows the person ------------------------------------


def test_a_late_nickname_leaves_the_identity_colour_untouched(connected):
    """A name arriving after the join must not make the colour jump.

    This is the ordinary case, not an edge one: the automatic /who (SPEC D15)
    relabels members moments after every join.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    before = weechat.state.buffers[connection.rooms["#general"]].nicks[ALICE[:8]][
        "color"
    ]
    deliver(
        rrc,
        connection,
        process,
        {
            "op": "chat",
            "kind": "notice",
            "src": "0" * 32,
            "body": f"members in #general: alice ({ALICE[:12]})",
        },
    )
    after = weechat.state.buffers[connection.rooms["#general"]].nicks["alice"]["color"]
    assert after == before


def test_two_members_sharing_an_identity_nickname_differ_in_colour(connected):
    """An impostor taking a nickname does not take the colour with it."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, "one", nick="alice")
    say(rrc, connection, process, BOB, "two", nick="alice")
    first, second = room_lines(weechat, connection)[-2:]
    assert first.startswith(weechat.info_get("nick_color", ALICE))
    assert second.startswith(weechat.info_get("nick_color", BOB))
    assert first.split(RESET)[0] != second.split(RESET)[0]


def test_one_identity_keeps_one_colour_across_every_surface(connected):
    """Nicklist, message line and private buffer agree on a person's colour."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": [ALICE]},
    )
    say(rrc, connection, process, ALICE, "hi", nick="alice")
    deliver(
        rrc,
        connection,
        process,
        {"op": "direct", "src": ALICE, "nick": "alice", "body": "p"},
    )
    nicklist = weechat.state.buffers[connection.rooms["#general"]].nicks["alice"][
        "color"
    ]
    assert nicklist == weechat.info_get("nick_color_name", ALICE)
    code = weechat.info_get("nick_color", ALICE)
    assert room_lines(weechat, connection)[-1].startswith(code)
    assert weechat.state.buffers[connection.dms[ALICE]].lines[-1].startswith(code)


# -- N4: the rendered shape ------------------------------------------------


def test_the_line_shape_is_code_then_name_then_reset_then_body(connected):
    """<code><name><reset>\\t<body>, in that order, with nothing between."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, "hello there", nick="alice")
    line = room_lines(weechat, connection)[-1]
    code = weechat.info_get("nick_color", ALICE)
    assert line.index(code) == 0
    assert line.index(RESET) == len(code) + len("alice")
    assert line[line.index(RESET) + 1] == "\t"


def test_the_body_shape_carries_no_formatting_at_all(connected):
    """A missing reset would tint the message text with the speaker's colour."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, ALICE, "hello there", nick="alice")
    body = room_lines(weechat, connection)[-1].split("\t", 1)[1]
    assert body == "hello there"
    for byte in (COLOUR, ATTRIBUTE, ESCAPE, RESET):
        assert byte not in body


# -- N5: injection ---------------------------------------------------------


def test_injection_of_a_colour_or_reset_by_a_hub_nickname_fails(connected):
    """A hub must not close our colour early and own the rest of the line.

    The nickname here carries both a raw colour code and a raw reset. Both are
    stripped, so the line holds exactly one of each and both are ours
    (SPEC D22, ACCEPTANCE C8 as amended).
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(
        rrc,
        connection,
        process,
        ALICE,
        body="body" + COLOUR + "F31" + RESET + "tail",
        nick="ev" + COLOUR + "il" + RESET + "admin",
    )
    line = room_lines(weechat, connection)[-1]
    assert len(room_lines(weechat, connection)) == 2, "an extra line was forged"
    assert line.count(COLOUR) == 1
    assert line.count(RESET) == 1
    assert line.startswith(weechat.info_get("nick_color", ALICE))
    assert COLOUR not in line.split("\t", 1)[1]
    assert RESET not in line.split("\t", 1)[1]


# -- N6: unknown or absent identity ---------------------------------------


def test_an_unknown_identity_still_renders_exactly_one_line(connected):
    """Somebody who never joined still gets a colour and a line."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc, connection, process, {"op": "joined", "room": "#general", "members": []}
    )
    say(rrc, connection, process, BOB, "hi")
    assert len(room_lines(weechat, connection)) == 2
    assert room_lines(weechat, connection)[-1].startswith(
        weechat.info_get("nick_color", BOB)
    )


def test_an_unknown_or_absent_identity_raises_nothing(connected):
    """A message with no src renders rather than failing.

    Real WeeChat answers info_get("nick_color_name", "") with "default", so
    there is no error path here and none may be invented.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "chat", "kind": "msg", "room": "#general", "body": "hi"},
    )
    assert len(room_lines(weechat, connection)) == 2
    assert room_lines(weechat, connection)[-1].endswith("\thi")
