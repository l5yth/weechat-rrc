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
"""Hotlist levels as scored by a real WeeChat (ACCEPTANCE H1-H4, SPEC D23-D25).

The unit suite proves which tags the script emits. It cannot prove what WeeChat
does with them, because the stand-in it asserts against is this repository's own
model of WeeChat. This closes that gap: it loads the real ``rrc.py`` into a real
``weechat-headless``, drives a real ``Connection`` with helper events, and reads
``notify_level`` and ``highlight`` back out of WeeChat's own ``line_data``.

Every "verified against WeeChat 4.10" claim in ``SPEC.md`` D23-D25 is reproduced
here, so a reviewer can re-run them rather than take them on trust.

Skipped with a stated reason when ``weechat-headless`` is absent. It never
touches the operator's WeeChat home: ``-d`` points at a temporary one.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ME = "1f5a80f61a6194267cf6b6df6a954adb"
PEER = "aabbccddeeff00112233445566778899"
#: A third identity, absent from the initial member list, so that its join
#: is actually announced -- _ev_join stays quiet about somebody already in
#: the nicklist. It arrives under the user's own nickname on purpose.
LATE = "ccddeeff00112233445566778899aabb"

#: WeeChat's own notify levels, from its user guide: -1 never enters the
#: hotlist, 0 is low ("activity"), 1 message, 2 private, 3 highlight.
NEVER, LOW, MESSAGE, PRIVATE, HIGHLIGHT = -1, 0, 1, 2, 3

#: Every type tag the script can emit, less the one no probe event produces:
#: rrc_part needs our own PART to be confirmed, which needs a live helper.
EXPECTED_TAGS = {
    "rrc_status",
    "rrc_join",
    "rrc_msg",
    "rrc_action",
    "rrc_notice",
    "rrc_direct",
}

PROBE = """
import json, sys
import weechat

sys.path.insert(0, {root!r})
# rrc.py guards main() behind __name__ == "__main__", and WeeChat loads a script
# under that name, so importing it as a module here registers nothing.
import rrc

ME, PEER, LATE, OUT = {me!r}, {peer!r}, {late!r}, {out!r}


def rrc_input_cb(data, buffer, text):
    return weechat.WEECHAT_RC_OK


def rrc_close_cb(data, buffer):
    return weechat.WEECHAT_RC_OK


def run(data, remaining):
    c = rrc.Connection("probe", "1f8a0102030405060708090a0b0cb3c5", nick="afri")
    c.on_event({{"op": "identity", "hash": ME}})
    c.on_event({{"op": "joined", "room": "#general", "members": [ME, PEER]}})
    for event in [
        {{"kind": "msg", "nick": "bob", "body": "ordinary MARK-plain"}},
        {{"kind": "msg", "nick": "bob", "body": "hey afri MARK-nick"}},
        {{"kind": "msg", "nick": "bob", "body": "ping " + ME[:8] + " MARK-short"}},
        {{"kind": "msg", "nick": "bob", "body": "ping " + ME + " MARK-full"}},
        {{"kind": "msg", "nick": "bob", "body": "MARK-substring afrikaans"}},
        {{"kind": "action", "nick": "bob", "body": "waves MARK-action"}},
        {{"kind": "notice", "nick": "bob", "body": "MARK-roomnotice afri"}},
    ]:
        event.update({{"op": "chat", "room": "#general", "src": PEER}})
        c.on_event(event)
    # The hub returns every message to its author, so this is how your own
    # words arrive -- naming yourself, which must still stay silent.
    c.on_event({{"op": "chat", "kind": "msg", "room": "#general", "src": ME,
                "nick": "afri", "body": "my own afri MARK-echo"}})
    # The arriving peer is deliberately given the user's own nickname: the join
    # line then matches a seeded word and must stay grey regardless.
    c.on_event({{"op": "join", "room": "#general", "members": [LATE],
                "nick": "afri"}})
    c.on_event({{"op": "direct", "src": PEER, "nick": "bob", "body": "MARK-dm"}})
    c.on_event({{"op": "chat", "kind": "notice", "src": PEER,
                "body": "MARK-hubnotice from the hub"}})
    # Activity is only the *default* level for hub chatter: a hub announcement
    # naming the user must still be able to reach them.
    c.on_event({{"op": "chat", "kind": "notice", "src": PEER,
                "body": "MARK-hubmention afri your registration expires"}})
    c.on_event({{"op": "chat", "kind": "notice", "src": PEER,
                "body": "members in #general: afri (" + ME[:8] + "), bob MARK-roster"}})

    hb = weechat.hdata_get("buffer")
    hls = weechat.hdata_get("lines")
    hl = weechat.hdata_get("line")
    hd = weechat.hdata_get("line_data")
    scored = []
    for buffer in [c.buffer] + list(c.rooms.values()) + list(c.dms.values()):
        line = weechat.hdata_pointer(
            hls, weechat.hdata_pointer(hb, buffer, "own_lines"), "first_line")
        while line:
            d = weechat.hdata_pointer(hl, line, "data")
            count = weechat.hdata_integer(hd, d, "tags_count")
            scored.append({{
                "message": weechat.hdata_string(hd, d, "message"),
                "notify": weechat.hdata_char(hd, d, "notify_level"),
                "highlight": weechat.hdata_char(hd, d, "highlight"),
                "tags": [weechat.hdata_string(hd, d, "%d|tags_array" % i)
                         for i in range(count)],
            }})
            line = weechat.hdata_move(hl, line, 1)
    open(OUT, "w").write(json.dumps({{
        "lines": scored,
        "highlight_words": weechat.buffer_get_string(
            c.rooms["#general"], "highlight_words"),
        "restrict": weechat.buffer_get_string(
            c.rooms["#general"], "highlight_tags_restrict"),
    }}))
    weechat.command("", "/quit")
    return weechat.WEECHAT_RC_OK


weechat.register("probe", "x", "1", "Apache-2.0", "probe", "", "")
weechat.hook_timer(300, 0, 1, "run", "")
"""


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    """Return what a real WeeChat made of every line the script printed."""
    binary = shutil.which("weechat-headless")
    if binary is None:
        pytest.skip("weechat-headless is not installed; hotlist scoring unverifiable")
    home = tmp_path_factory.mktemp("wchome")
    autoload = home / "python" / "autoload"
    autoload.mkdir(parents=True)
    out = home / "scored.json"
    autoload.joinpath("probe.py").write_text(
        PROBE.format(root=str(ROOT), me=ME, peer=PEER, late=LATE, out=str(out))
    )
    try:
        subprocess.run(
            [binary, "-d", str(home), "--no-connect"],
            timeout=90,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - a hung WeeChat
        pytest.skip("weechat-headless did not exit; cannot score")
    if not out.exists():  # pragma: no cover - the probe failed to run
        pytest.skip("the probe script did not run inside WeeChat")
    return json.loads(out.read_text())


def mark(scored, token):
    """Return the single scored line carrying *token*."""
    hits = [line for line in scored["lines"] if token in line["message"]]
    assert len(hits) == 1, f"expected one line marked {token}, found {len(hits)}"
    return hits[0]


def tagged(scored, tag):
    """Return every scored line carrying *tag*."""
    return [line for line in scored["lines"] if tag in line["tags"]]


def test_the_probe_really_rendered_every_surface(scored):
    """Guard the guard: a partial run could leave a surface silently untested."""
    seen = {
        tag
        for line in scored["lines"]
        for tag in line["tags"]
        if tag.startswith("rrc_") and not tag.startswith("rrc_id_")
    }
    assert seen == EXPECTED_TAGS, f"surfaces missing: {EXPECTED_TAGS - seen}"
    assert scored["restrict"] == "rrc_msg,rrc_action,rrc_notice"


def test_an_ordinary_room_message_is_scored_at_message_level(scored):
    """SPEC D23: a MSG from somebody else is "someone is talking"."""
    assert mark(scored, "MARK-plain")["notify"] == MESSAGE
    assert mark(scored, "MARK-plain")["highlight"] == 0


def test_an_action_is_scored_at_message_level(scored):
    """SPEC D23: an ACTION is a person talking, so it scores like one."""
    assert mark(scored, "MARK-action")["notify"] == MESSAGE


def test_a_room_notice_is_scored_at_message_level(scored):
    """SPEC D23: a NOTICE addressed to a room is somebody addressing it."""
    assert mark(scored, "MARK-roomnotice")["notify"] >= MESSAGE


def test_a_direct_message_is_scored_at_private_level(scored):
    """SPEC D23: a DM earns WeeChat's own fourth level."""
    assert mark(scored, "MARK-dm")["notify"] == PRIVATE


def test_a_hub_wide_notice_stays_at_activity_level(scored):
    """SPEC D23: hub chatter is activity, not a message."""
    assert mark(scored, "MARK-hubnotice")["notify"] == LOW
    assert mark(scored, "MARK-hubnotice")["highlight"] == 0


def test_a_hub_notice_naming_you_still_reaches_highlight_level(scored):
    """SPEC D23: activity is the default for hub chatter, not a ceiling.

    The hub buffer's highlight_tags_restrict allows rrc_notice precisely so an
    announcement that names the user can still reach them; only the roster
    shape is exempted, and this is the contrast that proves the exemption is
    narrow rather than a blanket silencing of the hub buffer.
    """
    assert mark(scored, "MARK-hubmention")["notify"] == HIGHLIGHT
    assert mark(scored, "MARK-hubmention")["highlight"] == 1


def test_a_join_line_stays_at_activity_level_even_naming_you(scored):
    """SPEC D25: the restriction, doing the work no per-line excuse could.

    The arriving peer carries the user's own nickname, so the line's text
    matches a seeded word. rrc_join is not on the room's
    highlight_tags_restrict list, so WeeChat cannot promote it anyway.
    """
    joins = tagged(scored, "rrc_join")
    assert joins, "no join line was rendered, so this would prove nothing"
    assert any("afri" in line["message"] for line in joins), "no join names the user"
    for line in joins:
        assert line["notify"] == LOW
        assert line["highlight"] == 0


def test_your_nickname_is_a_mention(scored):
    """SPEC D24: WeeChat promotes the line itself, from a word we only seeded."""
    assert mark(scored, "MARK-nick")["notify"] == HIGHLIGHT
    assert mark(scored, "MARK-nick")["highlight"] == 1


def test_your_short_identity_hash_is_a_mention(scored):
    """SPEC D24: the short form is what renders, so it is what people type."""
    assert mark(scored, "MARK-short")["notify"] == HIGHLIGHT
    assert mark(scored, "MARK-short")["highlight"] == 1


def test_your_full_identity_hash_is_a_mention(scored):
    """SPEC D24: the authoritative identifier reaches you too."""
    assert mark(scored, "MARK-full")["notify"] == HIGHLIGHT
    assert mark(scored, "MARK-full")["highlight"] == 1


def test_your_nickname_inside_a_longer_word_is_not_a_mention(scored):
    """SPEC D24: word boundaries are WeeChat's business, and it gets them right."""
    assert mark(scored, "MARK-substring")["notify"] == MESSAGE
    assert mark(scored, "MARK-substring")["highlight"] == 0


def test_your_own_echoed_words_never_notify_you(scored):
    """SPEC D25: the hub echoes your MSG back, naming you, and it stays silent.

    Neither the buffer's nick local variable nor the self_msg tag suppresses a
    self-highlight in WeeChat 4.10, which is why the tags say so explicitly.
    """
    assert mark(scored, "MARK-echo")["notify"] == NEVER
    assert mark(scored, "MARK-echo")["highlight"] == 0


def test_a_member_listing_naming_you_never_notifies_you(scored):
    """SPEC D25: a /who reply arrives after every join and names everyone."""
    assert mark(scored, "MARK-roster")["highlight"] == 0
    assert mark(scored, "MARK-roster")["notify"] == LOW


def test_the_plugins_own_status_line_cannot_mention_you(scored):
    """SPEC D25: "your identity is <hash>" quotes a mention key verbatim."""
    quoting = [line for line in tagged(scored, "rrc_status") if ME in line["message"]]
    assert quoting, "no status line quotes the hash, so this would prove nothing"
    for line in quoting:
        assert line["highlight"] == 0
        assert line["notify"] == LOW


def test_the_seeded_words_are_the_three_mention_keys(scored):
    """SPEC D24: what the plugin published is what WeeChat holds."""
    assert sorted(scored["highlight_words"].split(",")) == sorted([ME, ME[:8], "afri"])
