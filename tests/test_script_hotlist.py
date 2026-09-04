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
"""Hotlist activity levels (ACCEPTANCE H1-H6, SPEC D23-D27).

Before this feature every line the script printed carried no tags, so WeeChat
scored all of them ``low`` and the status bar showed one undifferentiated grey
whether somebody had joined, said something, or said your name. WeeChat scores
them itself from the tags asserted here; nothing in the script picks a hotlist
entry or a colour.
"""

from __future__ import annotations

ALICE = "1f5a80f61a6194267cf6b6df6a954adb"
BOB = "aabbccddeeff00112233445566778899"

#: Every type tag the vocabulary allows (``SPEC.md`` D26).
TYPE_TAGS = {
    "rrc_status",
    "rrc_join",
    "rrc_part",
    "rrc_msg",
    "rrc_action",
    "rrc_notice",
    "rrc_direct",
}


def deliver(rrc, connection, process, *events):
    """Push *events* through the helper pipe and let the script read them."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, "0")


def room(weechat, connection, name="#general"):
    """Return the recorded buffer for a room."""
    return weechat.state.buffers[connection.rooms[name]]


def notify_of(tags):
    """Return the WeeChat notify tags present in *tags*."""
    return [tag for tag in tags if tag.startswith("notify_")]


def types_of(tags):
    """Return the plugin type tags present in *tags*."""
    return [tag for tag in tags if tag in TYPE_TAGS]


def joined(rrc, connection, process, members=()):
    """Confirm our own join of #general so the room buffer exists."""
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": list(members)},
    )


def say(
    rrc,
    connection,
    process,
    src=ALICE,
    body="hi",
    kind="msg",
    room_name="#general",
    nick="alice",
):
    """Deliver one inbound chat event."""
    event = {"op": "chat", "kind": kind, "src": src, "body": body, "nick": nick}
    if room_name is not None:
        event["room"] = room_name
    deliver(rrc, connection, process, event)


# -- the oracle itself ----------------------------------------------------


def test_the_fake_records_tags_it_is_given(wee):
    """The stand-in must keep the tags, or nothing below tests anything.

    Tags are the whole of what this feature adds: WeeChat reads them to decide
    the hotlist level. A fake that accepted ``prnt_date_tags`` and dropped its
    third argument would let every assertion in this module pass while proving
    nothing, exactly as the colour fake once did by returning "".
    """
    weechat, rrc = wee
    pointer = weechat.buffer_new("probe", "", "", "", "")
    weechat.prnt_date_tags(pointer, 0, "rrc_msg,notify_message", "alice\thello")
    weechat.prnt(pointer, "untagged\tline")
    buffer = weechat.state.buffers[pointer]
    assert buffer.tags_for("hello") == ["rrc_msg", "notify_message"]
    assert buffer.tags_for("untagged") == []
    # The older suites read this; tagging must not move where text lands.
    assert buffer.text == ["hello", "line"]


# -- H1: every line carries a type tag and the right notify level ---------


def test_a_room_message_is_scored_at_message_level(connected):
    """A MSG in a room is what "someone is talking" means."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process)
    tags = room(weechat, connection).tags_for("hi")
    assert types_of(tags) == ["rrc_msg"]
    assert notify_of(tags) == ["notify_message"]


def test_an_action_is_scored_at_message_level(connected):
    """An ACTION is a person talking about themselves; still a message."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="waves", kind="action")
    tags = room(weechat, connection).tags_for("waves")
    assert types_of(tags) == ["rrc_action"]
    assert notify_of(tags) == ["notify_message"]


def test_a_room_notice_is_scored_at_message_level(connected):
    """A NOTICE addressed to a room is somebody talking to that room."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="build is green", kind="notice")
    tags = room(weechat, connection).tags_for("build is green")
    assert types_of(tags) == ["rrc_notice"]
    assert notify_of(tags) == ["notify_message"]


def test_a_hub_wide_notice_stays_at_activity_level(connected):
    """A room-less notice is hub chatter, not somebody addressing a room.

    This is where rrcd puts MOTDs and /who member listings, and SPEC D15 makes
    this plugin ask for a /who after every confirmed join and after every room
    of a post-outage rejoin. Scoring it as a message would light the hub buffer
    on traffic the plugin generated for itself.
    """
    weechat, rrc, connection, process = connected
    say(rrc, connection, process, body="motd: be nice", kind="notice", room_name=None)
    tags = weechat.state.buffers[connection.buffer].tags_for("motd: be nice")
    assert types_of(tags) == ["rrc_notice"]
    assert notify_of(tags) == [], "a room-less notice must not reach message level"


def test_a_received_direct_message_is_scored_at_private_level(connected):
    """A DM is addressed to you personally, so it earns its own level."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "direct", "src": BOB, "nick": "bob", "body": "psst"},
    )
    tags = weechat.state.buffers[connection.dms[BOB]].tags_for("psst")
    assert types_of(tags) == ["rrc_direct"]
    assert notify_of(tags) == ["notify_private"]


def test_a_join_and_a_part_stay_at_activity_level(connected):
    """Coming and going is activity; it is never a message."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    deliver(
        rrc,
        connection,
        process,
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    buffer = room(weechat, connection)
    assert types_of(buffer.tags_for("joined #general")) == ["rrc_join"]
    assert notify_of(buffer.tags_for("joined #general")) == []
    assert types_of(buffer.tags_for("left #general")) == ["rrc_part"]
    assert notify_of(buffer.tags_for("left #general")) == []


def test_our_own_join_and_part_stay_at_activity_level(connected):
    """Your own arrival and departure are activity too."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    deliver(rrc, connection, process, {"op": "parted", "room": "#general"})
    buffer = room(weechat, connection)
    assert types_of(buffer.tags_for("(0 present)")) == ["rrc_join"]
    assert notify_of(buffer.tags_for("(0 present)")) == []
    assert types_of(buffer.tags_for("left #general")) == ["rrc_part"]
    assert notify_of(buffer.tags_for("left #general")) == []


def test_a_plugin_status_line_stays_at_activity_level(connected):
    """Lines the plugin writes about itself never claim to be messages."""
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "state", "state": "up"},
        {"op": "pong", "lag_ms": 42},
        {"op": "error", "message": "hub said no"},
    )
    buffer = weechat.state.buffers[connection.buffer]
    for needle in ("up", "lag is 42ms", "hub said no"):
        assert types_of(buffer.tags_for(needle)) == ["rrc_status"]
        assert notify_of(buffer.tags_for(needle)) == []


def test_a_core_buffer_line_is_scored_at_activity_level(wee):
    """Even the core-buffer diagnostic carries a type tag, so /filter reaches it."""
    weechat, rrc = wee
    rrc.render.log("something happened")
    assert weechat.state.core_tags[-1] == "rrc_status"


def test_every_line_carries_one_type_tag_and_at_most_one_notify_level(connected):
    """The vocabulary is exhaustive and exclusive across every surface.

    A line with two type tags, or two notify tags, would make its hotlist level
    depend on WeeChat's tag ordering rather than on this plugin's intent.
    """
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process)
    say(rrc, connection, process, body="waves", kind="action")
    say(rrc, connection, process, body="notice me", kind="notice")
    say(rrc, connection, process, body="hub says", kind="notice", room_name=None)
    deliver(
        rrc,
        connection,
        process,
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "part", "room": "#general", "members": [BOB], "nick": "bob"},
        {"op": "direct", "src": BOB, "nick": "bob", "body": "psst"},
        {"op": "state", "state": "up"},
    )
    seen = set()
    for buffer in weechat.state.buffers.values():
        for tags in buffer.tags:
            parsed = tags.split(",") if tags else []
            assert len(types_of(parsed)) == 1, f"one type tag per line: {parsed}"
            assert len(notify_of(parsed)) <= 1, f"one notify tag at most: {parsed}"
            seen.update(types_of(parsed))
    assert seen == TYPE_TAGS, f"surfaces not exercised: {TYPE_TAGS - seen}"


# -- H4: your own words never notify you ----------------------------------


def test_your_own_direct_message_echo_is_kept_out_of_the_hotlist(connected):
    """The hub does not echo direct messages, so the script writes that line.

    Being the plugin's own rendering of the user's own words, it must not put
    the buffer in the hotlist and must not be able to highlight: verified
    against WeeChat 4.10, only notify_none with no_highlight achieves that.
    """
    weechat, rrc, connection, process = connected
    deliver(
        rrc,
        connection,
        process,
        {"op": "identity", "hash": ALICE},
        {"op": "direct", "src": BOB, "nick": "bob", "body": "psst"},
    )
    rrc.rrc_input_cb(f"{connection.name}/{rrc.DM_PREFIX}{BOB}", "", "hello there")
    tags = weechat.state.buffers[connection.dms[BOB]].tags_for("hello there")
    assert types_of(tags) == ["rrc_direct"]
    assert notify_of(tags) == ["notify_none"]
    assert "no_highlight" in tags
    assert "self_msg" in tags


# -- H5: a hub cannot forge a tag -----------------------------------------


def test_a_line_with_a_sender_carries_both_the_name_and_the_identity_tag(connected):
    """The conventional tag and the authoritative one, together (SPEC D26)."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, nick="alice")
    tags = room(weechat, connection).tags_for("hi")
    assert f"nick_alice" in tags
    assert f"rrc_id_{ALICE}" in tags


def test_a_line_with_no_sender_carries_neither(connected):
    """A status line is nobody talking, so it names nobody."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "state", "state": "up"})
    tags = weechat.state.buffers[connection.buffer].tags_for("up")
    assert not [t for t in tags if t.startswith(("nick_", "rrc_id_"))]


def test_two_senders_sharing_a_nickname_are_told_apart_by_the_identity_tag(connected):
    """A nickname is advisory and collides; an identity hash does not.

    This is what makes the identity tag worth its extra bytes: muting nick_bob
    mutes whoever currently answers to bob, while muting the identity tag mutes
    one person through any rename or impersonation (SPEC D21, D26).
    """
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, src=ALICE, body="first", nick="bob")
    say(rrc, connection, process, src=BOB, body="second", nick="bob")
    buffer = room(weechat, connection)
    assert "nick_bob" in buffer.tags_for("first")
    assert "nick_bob" in buffer.tags_for("second")
    assert f"rrc_id_{ALICE}" in buffer.tags_for("first")
    assert f"rrc_id_{BOB}" in buffer.tags_for("second")


def test_injection_of_a_notify_tag_by_a_hub_nickname_fails(connected):
    """A comma in a nickname must not become a tag separator.

    Reproduced against WeeChat 4.10 before this feature was designed: a tag
    list built by interpolating a nickname containing a comma yielded a real,
    effective notify_highlight, promoting the line to notify_level=3.
    """
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="payload", nick="bob,notify_highlight")
    tags = room(weechat, connection).tags_for("payload")
    assert notify_of(tags) == ["notify_message"], "the hub chose a notify level"
    assert "notify_highlight" not in tags
    assert types_of(tags) == ["rrc_msg"]


def test_injection_of_a_logging_or_filter_tag_by_a_hub_nickname_fails(connected):
    """no_log would hide a line from the user's logs; no_filter would pin it."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="payload", nick="eve,no_log,no_filter")
    tags = room(weechat, connection).tags_for("payload")
    assert "no_log" not in tags
    assert "no_filter" not in tags
    assert tags == [
        "rrc_msg",
        "notify_message",
        "nick_eveno_logno_filter",
        f"rrc_id_{ALICE}",
    ]


def test_injection_by_whitespace_in_a_hub_nickname_fails(connected):
    """Whitespace is a separator to anything else parsing the tag string."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="payload", nick="bob smith")
    tags = room(weechat, connection).tags_for("payload")
    assert "nick_bobsmith" in tags
    assert not [t for t in tags if any(ch.isspace() for ch in t)]


def test_injection_cannot_reach_the_identity_tag_either(connected):
    """The identity tag is hex by construction; it is sanitised regardless.

    SPEC D26 rests the identity tag's safety on a structural argument -- the
    helper hex-encodes the hash before it ever reaches this script. Sanitising
    it anyway costs nothing and means the claim does not depend on the helper.
    """
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, src="aabb,notify_highlight", body="payload")
    tags = room(weechat, connection).tags_for("payload")
    assert "notify_highlight" not in tags
    assert "rrc_id_aabbnotify_highlight" in tags


def test_injection_leaves_the_rendered_line_shape_untouched(connected):
    """Tags are a separate argument and never enter the message string.

    Layer C8 and SPEC D22 constrain the rendered line; this asserts the tag
    work did not disturb it, from the other side.
    """
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, body="payload", nick="bob,notify_highlight")
    line = room(weechat, connection).lines[-1]
    # Pinned exactly rather than by substring: the hub's nickname legitimately
    # contains the text "notify_highlight", so only the whole rendering shows
    # that no tag was concatenated into it.
    colour = weechat.info_get("nick_color", ALICE)
    assert line == f"{colour}bob,notify_highlight\x1c\tpayload"
    assert line.count("\n") == 0, "one buffer line"
    assert line.count("\x19") == 1, "one colour code, the plugin's own"
    assert line.count("\x1c") == 1, "one reset, immediately after the name"
    assert "rrc_msg" not in line, "the type tag never enters the message string"


# -- H2: WeeChat computes the mention; this plugin says who you are -------


def words(weechat, buffer):
    """Return the highlight words WeeChat holds for *buffer*."""
    return weechat.state.buffers[buffer].highlight_words


def test_the_three_mention_keys_are_seeded_on_a_room_buffer(connected):
    """Full hash, short hash and nickname: the three ways to mean "you"."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    seeded = words(weechat, connection.rooms["#general"])
    assert ALICE in seeded, "the full hash, for anyone who pastes it"
    assert ALICE[:8] in seeded, "the short form, which is what actually renders"
    assert "afri" in seeded, "the advisory nickname, how a human addresses you"


def test_keys_are_added_not_assigned_so_user_words_survive(connected):
    """A word the user added themselves must never be discarded (SPEC D24)."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    buffer = connection.rooms["#general"]
    weechat.buffer_set(buffer, "highlight_words_add", "deploy")
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    assert "deploy" in words(weechat, buffer), "the user's own word was discarded"
    assert ALICE in words(weechat, buffer)
    assigned = [
        v for prop, v in weechat.state.buffers[buffer].sets if prop == "highlight_words"
    ]
    assert assigned == [], "highlight_words was assigned wholesale"


def test_new_keys_replace_old_ones_when_the_nickname_changes(connected):
    """A rename retires the old key by name and publishes the new one."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    buffer = connection.rooms["#general"]
    assert "afri" in words(weechat, buffer)
    deliver(rrc, connection, process, {"op": "nick", "nick": "bandit"})
    assert "afri" not in words(weechat, buffer), "the stale nickname still mentions"
    assert "bandit" in words(weechat, buffer)
    assert ALICE in words(weechat, buffer), "the hash keys survive a rename"


def test_keys_reach_buffers_opened_before_the_identity_arrived(connected):
    """The hub buffer exists before the helper reports who we are.

    The irc plugin can seed at buffer creation alone because it knows the
    nickname first. Here it does not, so creation-time seeding would leave the
    hub buffer permanently unable to see the user.
    """
    weechat, rrc, connection, process = connected
    assert ALICE not in words(weechat, connection.buffer)
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    assert ALICE in words(weechat, connection.buffer)
    assert ALICE[:8] in words(weechat, connection.buffer)


def test_keys_reach_a_room_buffer_reopened_later(connected):
    """A buffer found again rather than created is seeded just the same."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    pointer = connection.rooms["#general"]
    connection.rooms.clear()
    assert connection.room_buffer("#general") == pointer
    assert ALICE in words(weechat, pointer)


def test_a_wildcard_in_a_hub_confirmed_nickname_is_stripped_from_the_keys(connected):
    """A hub answering /nick with "*" must not highlight every line.

    WeeChat reads "*" in highlight_words as a wildcard, and the nickname is
    whatever the hub confirms (SPEC D13), so unsanitised it would let the hub
    decide when the user's terminal beeps.
    """
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    deliver(rrc, connection, process, {"op": "nick", "nick": "*"})
    seeded = words(weechat, connection.rooms["#general"])
    assert "*" not in seeded
    assert not [word for word in seeded if "*" in word]
    assert ALICE in seeded, "the hash keys are unaffected"


def test_a_comma_in_a_nickname_cannot_seed_two_keys(connected):
    """highlight_words is comma-separated, so a comma would split the key."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    deliver(rrc, connection, process, {"op": "nick", "nick": "eve,root"})
    seeded = words(weechat, connection.rooms["#general"])
    assert seeded == ["everoot"]


def test_an_empty_nickname_contributes_no_keys(connected):
    """No nickname is not a key; seeding "" would be a word matching nothing."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "nick", "nick": ""})
    joined(rrc, connection, process)
    assert words(weechat, connection.rooms["#general"]) == []


def test_unchanged_keys_are_not_republished(connected):
    """Re-reporting the same identity must not churn every buffer."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    buffer = weechat.state.buffers[connection.rooms["#general"]]
    before = list(buffer.sets)
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    assert buffer.sets == before


# -- H3: only hub-supplied conversational content can mention you ---------


def restrict(weechat, buffer):
    """Return the tags WeeChat will let highlight on *buffer*."""
    return weechat.state.buffers[buffer].properties.get("highlight_tags_restrict", "")


def test_a_room_buffer_restricts_highlights_to_conversational_tags(connected):
    """Somebody talking can mention you; somebody arriving cannot."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    allowed = restrict(weechat, connection.rooms["#general"]).split(",")
    assert allowed == ["rrc_msg", "rrc_action", "rrc_notice"]
    assert "rrc_join" not in allowed and "rrc_part" not in allowed
    assert "rrc_status" not in allowed


def test_a_private_buffer_restricts_highlights_to_direct_messages(connected):
    """The only conversational content in a private buffer is the DM itself."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "direct", "src": BOB, "body": "psst"})
    assert restrict(weechat, connection.dms[BOB]) == "rrc_direct"


def test_a_hub_buffer_restricts_highlights_to_hub_notices(connected):
    """The plugin quotes your own keys at you here, so nothing else may match.

    "your identity is <hash>" and "you are now known as <nick>" each contain a
    mention key verbatim. Without the restriction the plugin would mention the
    user on its own status output (SPEC D25).
    """
    weechat, rrc, connection, process = connected
    assert restrict(weechat, connection.buffer) == "rrc_notice"


def test_a_status_line_quoting_your_own_key_is_restricted_from_highlighting(connected):
    """The structural proof: the tag such a line carries is not on the list."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    buffer = weechat.state.buffers[connection.buffer]
    line = [text for text in buffer.lines if ALICE in text]
    assert line, "the identity line really does quote the key back"
    assert types_of(buffer.tags_for(ALICE)) == ["rrc_status"]
    assert "rrc_status" not in restrict(weechat, connection.buffer)


def test_a_member_listing_is_restricted_from_highlighting(connected):
    """A roster names everyone, including you, after every single join.

    SPEC D15 makes a /who arrive after every join and every rejoin, so without
    this the feature would fire a mention on each one -- precisely the noise it
    exists to remove.
    """
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process, members=[ALICE, BOB])
    say(
        rrc,
        connection,
        process,
        src=BOB,
        kind="notice",
        room_name=None,
        body=f"members in #general: afri ({ALICE[:8]}), bob ({BOB[:8]})",
    )
    tags = weechat.state.buffers[connection.buffer].tags_for("members in #general")
    assert "no_highlight" in tags
    assert types_of(tags) == ["rrc_notice"]


def test_an_ordinary_hub_notice_is_not_restricted_from_highlighting(connected):
    """Only the roster shape is exempt; a hub announcement may still reach you."""
    weechat, rrc, connection, process = connected
    say(
        rrc,
        connection,
        process,
        src=BOB,
        kind="notice",
        room_name=None,
        body="hub restarting in 5 minutes",
    )
    tags = weechat.state.buffers[connection.buffer].tags_for("hub restarting")
    assert "no_highlight" not in tags


# -- H4: your own words never notify you ----------------------------------


def test_your_own_room_message_echoed_by_the_hub_never_notifies_you(connected):
    """The hub returns every message to its author, so this arrives inbound."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    say(rrc, connection, process, src=ALICE, body="my own words", nick="afri")
    tags = room(weechat, connection).tags_for("my own words")
    assert notify_of(tags) == ["notify_none"]
    assert "no_highlight" in tags, "typing your own name must not highlight you"
    assert "self_msg" in tags


def test_your_own_echoed_action_never_notifies_you(connected):
    """An ACTION of yours comes back the same way a MSG does."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    say(rrc, connection, process, src=ALICE, body="waves", kind="action")
    tags = room(weechat, connection).tags_for("waves")
    assert notify_of(tags) == ["notify_none"]
    assert "no_highlight" in tags


def test_another_persons_message_is_not_treated_as_your_echo(connected):
    """The suppression is keyed on the sender, never on the text."""
    weechat, rrc, connection, process = connected
    deliver(rrc, connection, process, {"op": "identity", "hash": ALICE})
    joined(rrc, connection, process)
    say(rrc, connection, process, src=BOB, body="hello afri", nick="bob")
    tags = room(weechat, connection).tags_for("hello afri")
    assert notify_of(tags) == ["notify_message"]
    assert "no_highlight" not in tags


def test_an_echo_arriving_before_the_identity_is_known_is_not_silenced(connected):
    """With no identity yet there is nothing to compare against, and no guess."""
    weechat, rrc, connection, process = connected
    joined(rrc, connection, process)
    say(rrc, connection, process, src="", body="anonymous", nick="ghost")
    tags = room(weechat, connection).tags_for("anonymous")
    assert notify_of(tags) == ["notify_message"]
