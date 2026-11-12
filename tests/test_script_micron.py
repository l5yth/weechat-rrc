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
"""Micron formatting in message bodies, as NomadNet draws it.

NomadNet writes Micron's tag character as U+00A6 BROKEN BAR in RRC messages.
A message carrying a formatting tag in that form is drawn in WeeChat colours;
every other message is shown exactly as sent. The text is cleaned first, so
every formatting byte in a rendered body is one this script asked WeeChat for,
from a closed vocabulary, and none came from the hub.
"""

from __future__ import annotations

import random
import re

import pytest

PEER = "1f5a80f61a6194267cf6b6df6a954adb"
ME = "9c7e0102030405060708090a0b0c4a2f"

#: Control characters and the broken bar, built rather than written, so this
#: source file stays free of them.
NL, TAB, ESC, BAR = chr(10), chr(9), chr(27), chr(0xA6)

#: WeeChat's own formatting bytes: colour, set attribute, remove attribute,
#: reset.
COLOUR, ATTRIBUTE, REMOVE, RESET = chr(25), chr(26), chr(27), chr(28)

#: Every argument the script may hand to ``weechat.color`` while rendering
#: Micron: a foreground and background pair of xterm indices or ``default``,
#: an attribute switched on or off, or a reset.
VOCABULARY = re.compile(
    r"^((1[6-9]|[2-9]\d|1\d\d|2[0-4]\d|25[0-5]|default),"
    r"(1[6-9]|[2-9]\d|1\d\d|2[0-4]\d|25[0-5]|default)"
    r"|-?(bold|italic|underline)|reset)$"
)


def deliver(rrc, connection, process, *events):
    """Push *events* down the helper pipe and let the script read them."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, str(process.stdout.fileno()))


@pytest.fixture
def room(connected):
    """Return ``(weechat, rrc, render)`` where *render* shows one room message.

    *render* delivers a message with the given body to ``#art`` and returns
    the body as printed, everything after the prefix separator.
    """
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "joined", "room": "#art", "members": []})

    def render(body, kind="msg"):
        """Deliver *body* as a *kind* message and return what was printed."""
        deliver(
            rrc,
            connection,
            process,
            {"op": "chat", "kind": kind, "room": "#art", "src": PEER, "body": body},
        )
        line = weechat.state.buffers[connection.rooms["#art"]].lines[-1]
        return line.split(TAB, 1)[1]

    return weechat, rrc, render


# -- the colour map --------------------------------------------------------


@pytest.mark.parametrize(
    ("rgb", "index"),
    [
        ((255, 0, 0), 196),  # pure red, a cube corner
        ((0, 0, 0), 16),  # black is the cube's, not the ramp's
        ((255, 255, 255), 231),  # and so is white
        ((128, 128, 128), 244),  # mid grey lands on the ramp exactly
        ((0, 255, 0), 46),
        ((0, 0, 255), 21),
        ((95, 135, 175), 67),  # an exact interior cube point
        ((3, 3, 3), 16),  # below the ramp: nearest is cube black
        ((8, 8, 8), 232),  # the ramp's first step
        ((238, 238, 238), 255),  # and its last
        ((16, 32, 48), 234),  # a dark blue-grey is nearer the ramp
    ],
)
def test_colours_map_to_the_nearest_xterm_index(wee, rgb, index):
    """The cube and the greyscale ramp are both candidates, nearest wins."""
    _, rrc = wee
    assert rrc.xterm256(*rgb) == index


def test_every_rgb_value_maps_into_the_256_colour_range(wee):
    """No input escapes 16-255: the 16 system colours are user-themed."""
    _, rrc = wee
    for value in range(0, 256, 17):
        for other in (0, 128, 255):
            assert 16 <= rrc.xterm256(value, other, 255 - value) <= 255


# -- detection -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "hello there",
        "run `ls -la` and `pwd`",
        "`F00raw backtick micron`f",
        "price " + BAR + " 5",
        "trailing bar " + BAR,
        BAR + "Fzzz not hex",
        BAR + "FT12345 too short",
        BAR + "c" + BAR + "<" + BAR + "= layout tags alone",
        BAR + "` a reset alone",
        BAR + "Q unknown",
    ],
)
def test_text_without_a_formatting_tag_is_shown_exactly_as_sent(room, body):
    """Only a broken-bar formatting tag turns rendering on.

    Raw backtick Micron is not interpreted: in plain prose a backtick is far
    more often code than a tag.
    """
    _, _, render = room
    assert render(body) == body


@pytest.mark.parametrize(
    "tag", ["F123", "B123", "FTa0b1c2", "BTA0B1C2", "f", "b", "!", "*", "_"]
)
def test_each_formatting_tag_turns_rendering_on(room, tag):
    """A colour, a default colour, or an attribute toggle is enough."""
    _, _, render = room
    assert render(BAR + tag + "x").endswith("x" + RESET)


# -- rendering -------------------------------------------------------------


def test_a_twelve_bit_background_and_its_default(room):
    """Each digit is doubled (f -> ff), and ``b`` restores the background."""
    weechat, _, render = room
    assert render(BAR + "Bf00  " + BAR + "b x") == (
        weechat.color("default,196")
        + "  "
        + weechat.color("default,default")
        + " x"
        + RESET
    )


def test_a_twenty_four_bit_colour_takes_the_nearest_index(room):
    """``FT`` and ``BT`` carry six hex digits, in either case."""
    weechat, rrc, render = room
    assert render(BAR + "FT102030a" + BAR + "BTff0000b") == (
        weechat.color(f"{rrc.xterm256(16, 32, 48)},default")
        + "a"
        + weechat.color(f"{rrc.xterm256(16, 32, 48)},196")
        + "b"
        + RESET
    )


def test_foreground_and_background_are_tracked_together(room):
    """Changing one keeps the other; ``f`` resets only the foreground."""
    weechat, _, render = room
    assert render(BAR + "F0f0" + BAR + "B00fx" + BAR + "fy") == (
        weechat.color("46,default")
        + weechat.color("46,21")
        + "x"
        + weechat.color("default,21")
        + "y"
        + RESET
    )


def test_attributes_toggle(room):
    """``!``, ``*`` and ``_`` switch bold, italic and underline on, then off."""
    weechat, _, render = room
    assert render(
        BAR + "!b" + BAR + "! " + BAR + "*i" + BAR + "* " + BAR + "_u" + BAR + "_"
    ) == (
        weechat.color("bold")
        + "b"
        + weechat.color("-bold")
        + " "
        + weechat.color("italic")
        + "i"
        + weechat.color("-italic")
        + " "
        + weechat.color("underline")
        + "u"
        + weechat.color("-underline")
        + RESET
    )


def test_a_reset_tag_clears_colour_and_attributes(room):
    """After a reset the next toggle switches on again, and colours default."""
    weechat, _, render = room
    assert render(
        BAR + "Ff00" + BAR + "!a" + BAR + "`b" + BAR + "!c" + BAR + "B000d"
    ) == (
        weechat.color("196,default")
        + weechat.color("bold")
        + "a"
        + RESET
        + "b"
        + weechat.color("bold")
        + "c"
        + weechat.color("default,16")
        + "d"
        + RESET
    )


def test_layout_field_and_link_tags_are_dropped(room):
    """They mean nothing on one buffer line, so nothing of them is printed."""
    weechat, _, render = room
    dropped = "".join(BAR + tag for tag in "<>{clra=")
    assert render(BAR + "Ff00" + dropped + "x") == (
        weechat.color("196,default") + "x" + RESET
    )


def test_an_unknown_tag_is_kept_as_written(room):
    """A broken bar before anything else is text, not a tag."""
    weechat, _, render = room
    assert render(BAR + "Ff00" + BAR + "Q " + BAR + "Fzz") == (
        weechat.color("196,default") + BAR + "Q " + BAR + "Fzz" + RESET
    )


def test_block_characters_pass_through_whole(room):
    """Colour art is mostly U+2580-U+259F; it must come out unchanged."""
    weechat, rrc, render = room
    art = chr(0x2584) + chr(0x2580) + chr(0x2588)
    purple = rrc.xterm256(0x44, 0x00, 0x77)
    assert render(BAR + "B000" + BAR + "F407" + art) == (
        weechat.color("default,16") + weechat.color(f"{purple},16") + art + RESET
    )


# -- where it applies ------------------------------------------------------


def test_actions_notices_and_direct_messages_are_rendered_too(connected):
    """Every surface that shows somebody's words renders their Micron."""
    weechat, rrc, connection, process = connected
    styled = BAR + "!hi"
    expected = weechat.color("bold") + "hi" + RESET
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#art", "members": []},
        {"op": "chat", "kind": "action", "room": "#art", "src": PEER, "body": styled},
        {"op": "chat", "kind": "notice", "room": "#art", "src": PEER, "body": styled},
        {"op": "direct", "src": PEER, "nick": "bob", "body": styled},
    )
    room = weechat.state.buffers[connection.rooms["#art"]].lines
    assert room[-2].endswith(" " + expected)
    assert room[-1].endswith(": " + expected)
    dm = weechat.state.buffers[connection.dms[PEER]].lines[-1]
    assert dm.endswith(TAB + expected)


def test_your_own_direct_echo_is_rendered_like_the_peer_sees_it(connected):
    """The hub does not echo direct messages, so the local echo renders."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ME})
    dm = connection.dm_buffer(PEER)
    rrc.rrc_input_cb(f"{connection.name}/@{PEER}", dm, BAR + "Ff00red")
    assert (
        weechat.state.buffers[dm]
        .lines[-1]
        .endswith(TAB + weechat.color("196,default") + "red" + RESET)
    )
    assert process.written[-1]["text"] == BAR + "Ff00red", "sent as typed"


def test_the_option_turns_rendering_off(room):
    """``micron off`` shows the tags exactly as they were sent."""
    weechat, _, render = room
    weechat.config_set_plugin("micron", "off")
    assert render(BAR + "Ff00red" + BAR + "f") == BAR + "Ff00red" + BAR + "f"


def test_the_option_defaults_to_on(wee):
    """Registration seeds ``micron`` as ``on``, like the other options."""
    weechat, rrc = wee
    rrc.main()
    assert weechat.config_get_plugin("micron") == "on"


def test_the_line_keeps_its_tags(connected):
    """Rendering changes the body, never what WeeChat scores the line by."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#art", "members": []},
        {"op": "chat", "kind": "msg", "room": "#art", "src": PEER, "body": BAR + "!x"},
    )
    tags = weechat.state.buffers[connection.rooms["#art"]].tags_for("x")
    assert "rrc_msg" in tags and "notify_message" in tags


# -- injection -------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        BAR + "Ff00a" + COLOUR + "F31b",
        BAR + "Ff00a" + RESET + "b",
        BAR + "!a" + ATTRIBUTE + chr(1) + "b",
        BAR + "!a" + REMOVE + chr(1) + "b",
        BAR + "Ff00line one" + NL + "forged second line",
        BAR + "Ff00" + ESC + "[31mansi",
        BAR + "F" + COLOUR + "F31",
        BAR + "FT" + COLOUR + "lightred",
    ],
)
def test_control_characters_never_survive_rendering(connected, hostile):
    """Formatting in the body is the script's own: one reset, nothing forged.

    Hub text is cleaned before it is rendered, so a raw colour, attribute,
    reset, escape or newline is gone before a tag is read, and a tag spliced
    around one cannot smuggle it back.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#art", "members": []},
        {"op": "chat", "kind": "msg", "room": "#art", "src": PEER, "body": hostile},
    )
    lines = weechat.state.buffers[connection.rooms["#art"]].lines
    assert len(lines) == 2, "a newline forged an extra buffer line"
    body = lines[-1].split(TAB, 1)[1]
    cleaned = rrc.clean(hostile)
    assert body == rrc.formatted(cleaned), "rendered before it was cleaned"
    assert body.count(RESET) == (1 if rrc.MICRON_FORMAT.search(cleaned) else 0)
    for byte in (NL, ESC + "[", COLOUR + "F31", RESET + "b"):
        assert byte not in body, "hub text kept a formatting byte"


def test_only_the_closed_vocabulary_reaches_weechat_color(room, monkeypatch):
    """Random hub text, tags and control bytes alike, asks for nothing else.

    WeeChat's ``color()`` also accepts option names and raw codes, so an
    argument the hub could steer would be a way to restyle beyond the body.
    Every argument is recorded and checked against the vocabulary.
    """
    weechat, rrc, render = room
    asked = []
    real = weechat.color

    def recording(name):
        """Record *name* and answer as the fake does."""
        asked.append(name)
        return real(name)

    monkeypatch.setattr(weechat, "color", recording)
    alphabet = [BAR] * 6 + list("FBTfb!*_`<>{clra=0123456789abcdefABCDEFxyz ,")
    alphabet += [COLOUR, ATTRIBUTE, REMOVE, RESET, NL, chr(0), chr(0x2584)]
    rng = random.Random(0xA6)
    for _ in range(300):
        body = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        shown = render(body)
        for byte in (NL, chr(0)):
            assert byte not in shown
    assert any(name[0].isdigit() for name in asked), "no colour was rendered"
    assert "bold" in asked, "no toggle was rendered"
    for name in asked:
        assert VOCABULARY.match(name), f"asked WeeChat for {name!r}"
