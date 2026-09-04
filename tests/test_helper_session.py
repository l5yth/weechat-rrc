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
"""RRC session state machine tests.

Covers the session shape from 2-RRC: HELLO first, WELCOME opens the session,
room membership is ephemeral, and nothing survives a Link drop.
"""

from __future__ import annotations

import pytest

from rrc.helper import constants as C
from rrc.helper import envelope as E
from rrc.helper import session as S
from tests.conftest import Harness

# -- helpers ---------------------------------------------------------------


def test_normalise_room_lowercases_and_trims():
    """Rooms match case-insensitively; hubs do not preserve casing."""
    assert S.normalise_room("  #General  ") == "#general"


@pytest.mark.parametrize(
    "value,expected",
    [
        ({1: True, 2: True, 3: False}, {1, 2}),
        ([1, 2], {1, 2}),
        ((1, 2), {1, 2}),
        ({1: True, "x": True}, {1}),
        ([1, "two", True], {1}),
        ("nonsense", set()),
        (None, set()),
    ],
)
def test_parse_caps_accepts_both_shapes(value, expected):
    """Capabilities may be a map or a list; anything else yields nothing."""
    assert S.parse_caps(value) == expected


def test_sanitise_strips_control_characters():
    """Hub-supplied text cannot carry newlines or terminal escapes.

    Only the control characters are removed. An escape sequence loses its
    leading ESC and its printable remainder survives as literal text, which is
    the point: it is displayed rather than interpreted by the terminal.
    """
    assert S.sanitise("a\x00b\nc\x1b[31md\x7f") == "abc[31md"
    assert "\x1b" not in S.sanitise("\x1b[31mred")


def test_sanitise_keeps_printable_unicode():
    """Legitimate non-ASCII text survives sanitisation."""
    assert S.sanitise("学习 café") == "学习 café"


@pytest.mark.parametrize("value", [None, 42, b"bytes", ["list"]])
def test_sanitise_returns_the_fallback_for_non_text(value):
    """A field that is not text yields the fallback, not a crash."""
    assert S.sanitise(value) is None
    assert S.sanitise(value, fallback="") == ""


# -- handshake -------------------------------------------------------------


def test_start_sends_hello_first(harness):
    """2-RRC: the first message a client sends is a HELLO."""
    harness.session.start()
    assert harness.last.type == C.T_HELLO
    assert harness.last.body[C.B_HELLO_NAME] == S.CLIENT_NAME
    assert harness.last.body[C.B_HELLO_VER] == S.CLIENT_VERSION


def test_hello_does_not_advertise_resource_transfer(harness):
    """SPEC.md D7: not advertising the capability is how a client opts out."""
    harness.session.start()
    caps = harness.last.body[C.B_HELLO_CAPS]
    assert C.CAP_RESOURCE_ENVELOPE not in caps
    assert caps[C.CAP_ACTION] is True
    assert caps[C.CAP_DIRECT_NOTICE] is True


def test_welcome_opens_the_session(harness):
    """The session is only usable once WELCOME arrives."""
    assert not harness.session.ready
    harness.welcome(limits={C.B_LIMIT_MAX_MSG_BODY_BYTES: 350})
    assert harness.session.ready
    event = harness.ops("welcome")[0]
    assert event["hub"] == "TestHub"
    assert event["caps"] == [1, 2]
    assert event["limits"] == {"max_msg_body_bytes": 350}


def test_welcome_with_no_body_still_opens_the_session(harness):
    """2-RRC: clients must not require any particular WELCOME field."""
    harness.feed(C.T_WELCOME)
    assert harness.session.ready
    assert harness.ops("welcome")[0]["hub"] is None


def test_welcome_sanitises_the_hub_name(harness):
    """A hub name is attacker-controlled text and is cleaned before display."""
    harness.feed(C.T_WELCOME, body={C.B_WELCOME_HUB: "Evil\nHub\x1b[31m"})
    assert harness.ops("welcome")[0]["hub"] == "EvilHub[31m"


# -- rooms -----------------------------------------------------------------


def test_join_then_joined_confirms_membership(ready):
    """Our own JOINED carries the full member list and confirms the join."""
    ready.session.join("#General")
    assert ready.last.type == C.T_JOIN
    assert ready.last.room == "#general"
    ready.feed(C.T_JOINED, room="#general", body=[Harness.ME, Harness.PEER])
    assert ready.session.rooms == {"#general"}
    event = ready.ops("joined")[0]
    assert event["members"] == [Harness.ME.hex(), Harness.PEER.hex()]


def test_joined_for_someone_else_is_reported_separately(ready):
    """Once we are in a room, a JOINED means somebody else arrived."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[Harness.ME])
    ready.feed(C.T_JOINED, room="#general", body=[Harness.PEER], nick="bob")
    event = ready.ops("join")[0]
    assert event["members"] == [Harness.PEER.hex()]
    assert event["nick"] == "bob"


def test_join_is_ignored_when_already_a_member(ready):
    """Rejoining a room we are already in sends nothing."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[Harness.ME])
    before = len(ready.raw)
    ready.session.join("#GENERAL")
    assert len(ready.raw) == before


def test_part_then_parted_clears_membership(ready):
    """Our own PARTED removes the room from local state."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[Harness.ME])
    ready.session.part("#general")
    assert ready.last.type == C.T_PART
    ready.feed(C.T_PARTED, room="#general")
    assert ready.session.rooms == set()
    assert ready.ops("parted")[0]["room"] == "#general"


def test_parted_for_someone_else_is_reported_separately(ready):
    """An unsolicited PARTED means another member left."""
    ready.feed(C.T_PARTED, room="#general", body=[Harness.PEER], nick="bob")
    event = ready.ops("part")[0]
    assert event["members"] == [Harness.PEER.hex()]
    assert event["nick"] == "bob"


@pytest.mark.parametrize("msg_type", [C.T_JOINED, C.T_PARTED])
def test_membership_events_without_a_room_are_ignored(ready, msg_type):
    """A membership event with no room name cannot be acted on."""
    ready.feed(msg_type, body=[Harness.PEER])
    assert ready.ops("join") == [] and ready.ops("part") == []


@pytest.mark.parametrize("body", [None, "text", 42, [1, "two"]])
def test_member_lists_that_are_not_hashes_are_ignored(ready, body):
    """2-RRC: the member list is advisory and may be absent or unusable."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=body)
    assert ready.ops("joined")[0]["members"] == []


def test_room_limit_is_enforced_before_sending(harness):
    """Joining past the hub's room limit is refused locally."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_MAX_ROOMS_PER_SESSION: 1})
    harness.session.join("#one")
    harness.feed(C.T_JOINED, room="#one", body=[Harness.ME])
    harness.session.join("#two")
    assert "at most 1 rooms" in harness.ops("error")[0]["message"]


def test_oversized_room_name_is_refused(harness):
    """A room name past the hub's limit is refused with the byte count."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_MAX_ROOM_NAME_BYTES: 4})
    harness.session.join("#toolong")
    assert "room is 8 bytes" in harness.ops("error")[0]["message"]


# -- chat ------------------------------------------------------------------


def test_say_sends_a_msg_with_the_nickname(ready):
    """Room content carries the advisory nickname as a display hint."""
    ready.session.say("#general", "hello")
    assert ready.last.type == C.T_MSG
    assert ready.last.room == "#general"
    assert ready.last.body == "hello"
    assert ready.last.nick == "afri"


def test_action_sends_an_action(ready):
    """``/me`` maps to ACTION, not to a MSG with markup."""
    ready.session.action("#general", "waves")
    assert ready.last.type == C.T_ACTION
    assert ready.last.body == "waves"


def test_notice_kind_is_supported(ready):
    """NOTICE is available as a distinct kind of room content."""
    ready.session.say("#general", "fyi", kind=C.T_NOTICE)
    assert ready.last.type == C.T_NOTICE


def test_oversized_body_is_refused_before_sending(harness):
    """A message the hub would reject is stopped locally, with the numbers."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_MAX_MSG_BODY_BYTES: 4})
    harness.session.say("#general", "far too long")
    assert harness.ops("error")[0]["message"].startswith("body is 12 bytes")
    assert harness.last.type == C.T_HELLO  # nothing was sent


@pytest.mark.parametrize(
    "msg_type,kind", [(C.T_MSG, "msg"), (C.T_NOTICE, "notice"), (C.T_ACTION, "action")]
)
def test_incoming_room_content_is_reported(ready, msg_type, kind):
    """Each chat type is reported with its kind so it can be rendered."""
    ready.feed(msg_type, room="#General", src=Harness.PEER, body="hi", nick="bob")
    event = ready.ops("chat")[0]
    assert event["kind"] == kind
    assert event["room"] == "#general"
    assert event["src"] == Harness.PEER.hex()
    assert event["nick"] == "bob"
    assert event["body"] == "hi"


def test_incoming_chat_is_sanitised(ready):
    """Message bodies and nicknames cannot forge buffer lines."""
    ready.feed(C.T_MSG, room="#x", src=Harness.PEER, body="a\nb", nick="e\x1bvil")
    event = ready.ops("chat")[0]
    assert event["body"] == "ab"
    assert event["nick"] == "evil"


def test_incoming_chat_without_a_room_is_reported_with_none(ready):
    """A roomless chat message is still surfaced rather than dropped."""
    ready.feed(C.T_MSG, src=Harness.PEER, body="hi")
    assert ready.ops("chat")[0]["room"] is None


def test_incoming_chat_with_a_non_text_body_becomes_empty(ready):
    """A structured payload we cannot render degrades to an empty string."""
    ready.feed(C.T_MSG, room="#x", src=Harness.PEER, body={1: 2})
    assert ready.ops("chat")[0]["body"] == ""


# -- ping, errors, teardown -------------------------------------------------


def test_ping_measures_the_round_trip(ready):
    """A PONG echoing our token yields a lag figure."""
    ready.session.ping()
    token = ready.last.body
    ready.now += 0.25
    ready.feed(C.T_PONG, body=token)
    assert ready.ops("pong")[0]["lag_ms"] == 250


def test_unsolicited_pong_is_ignored(ready):
    """A PONG we never asked for produces no lag reading."""
    ready.feed(C.T_PONG, body=b"\x00" * 8)
    assert ready.ops("pong") == []


def test_pong_with_no_token_is_ignored(ready):
    """A PONG with no correlatable body is ignored."""
    ready.session.ping()
    ready.feed(C.T_PONG)
    assert ready.ops("pong") == []


def test_incoming_ping_is_answered_with_the_body_echoed(ready):
    """3-RRC: the PONG body must echo the PING body unchanged."""
    ready.feed(C.T_PING, body=b"token123")
    assert ready.last.type == C.T_PONG
    assert ready.last.body == b"token123"


def test_hub_errors_are_surfaced(ready):
    """An ERROR from the hub is shown to the user in plain language."""
    ready.feed(C.T_ERROR, body="Rate limit exceeded.")
    assert ready.ops("error")[0]["message"] == "Rate limit exceeded."


def test_hub_error_without_text_still_reports(ready):
    """A structured or empty ERROR still produces something readable."""
    ready.feed(C.T_ERROR, body={1: 2})
    assert ready.ops("error")[0]["message"] == "hub reported an error"


def test_client_to_hub_types_from_the_hub_are_ignored(ready):
    """A hub sending a client-only type is ignored, not acted on."""
    before = len(ready.events)
    ready.feed(C.T_HELLO, body={})
    ready.feed(C.T_JOIN, room="#x")
    ready.feed(C.T_PART, room="#x")
    assert len(ready.events) == before


def test_undecodable_frames_are_ignored(ready):
    """A frame decode() rejects never reaches a handler."""
    before = len(ready.events)
    ready.session.on_frame(b"\xff\xff\xff")
    assert len(ready.events) == before


def test_link_down_clears_all_session_state(ready):
    """1-RRC: membership evaporates with the Link; nothing is replayed."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[Harness.ME])
    ready.session.ping()
    ready.session.on_link_down("link timed out")
    assert ready.session.rooms == set()
    assert not ready.session.ready
    event = ready.ops("state")[-1]
    assert event["state"] == "down"
    assert event["reason"] == "link timed out"


def test_set_nick_updates_the_advisory_label(ready):
    """A nickname change rides along on the next envelope, not a NICK message."""
    before = len(ready.raw)
    ready.session.set_nick("newnick")
    assert len(ready.raw) == before  # nothing sent
    ready.session.say("#general", "hi")
    assert ready.last.nick == "newnick"
    assert ready.ops("nick")[0]["nick"] == "newnick"


def test_oversized_nick_is_refused(harness):
    """A nickname past the hub's limit is refused and does not take effect."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_MAX_NICK_BYTES: 4})
    harness.session.set_nick("far too long")
    assert harness.session.nick is None
    assert "nick is 12 bytes" in harness.ops("error")[0]["message"]


def test_rate_limit_refuses_and_recovers(harness):
    """Sending past the hub's rate limit is refused until the window rolls."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE: 2})
    harness.session.say("#x", "one")
    harness.session.say("#x", "two")
    harness.session.say("#x", "three")
    assert "2 messages per minute" in harness.ops("error")[0]["message"]
    harness.now += 61.0
    harness.session.say("#x", "four")
    assert harness.last.body == "four"


def test_no_rate_limit_means_no_local_throttling(ready):
    """With no advertised rate limit, the client does not throttle itself."""
    for i in range(50):
        ready.session.say("#x", str(i))
    assert ready.ops("error") == []
