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
"""Direct message conformance (ACCEPTANCE C4).

Direct delivery is an ``EX1`` extension, not core RRC. Two things must hold: the
envelope shape is exactly what the reference hub expects, and a hub that never
advertised the capability produces a clear refusal rather than silence.
"""

from __future__ import annotations

import cbor2
import pytest

from rrc.helper import constants as C
from tests.conftest import Harness


def test_direct_notice_has_dst_and_omits_room(ready):
    """EX1: K_DST is set and K_ROOM must be absent, or the hub rejects it."""
    ready.session.direct(Harness.PEER, "hello")
    raw = cbor2.loads(ready.raw[-1])
    assert raw[C.K_T] == C.T_NOTICE
    assert raw[C.K_DST] == Harness.PEER
    assert C.K_ROOM not in raw
    assert raw[C.K_BODY] == "hello"


def test_direct_uses_a_full_identity_hash(ready):
    """EX1: nicknames and hash prefixes are not accepted in K_DST."""
    ready.session.direct(Harness.PEER[:8], "hello")
    assert "16-byte identity hash" in ready.ops("error")[0]["message"]
    assert ready.ops("chat") == []


def test_direct_is_refused_without_the_capability(harness):
    """A hub that did not advertise CAP_DIRECT_NOTICE gets no such envelope."""
    harness.session.start()
    harness.welcome(caps=(C.CAP_ACTION,))
    before = len(harness.raw)
    harness.session.direct(Harness.PEER, "hello")
    assert len(harness.raw) == before  # nothing was sent
    assert "does not support direct messages" in harness.ops("error")[0]["message"]


def test_direct_is_refused_before_welcome(harness):
    """Capabilities are unknown until WELCOME, so direct messages wait."""
    harness.session.start()
    harness.session.direct(Harness.PEER, "hello")
    assert "does not support direct messages" in harness.ops("error")[0]["message"]


def test_direct_respects_the_body_limit(harness):
    """A direct message past the body limit is refused like any other."""
    harness.session.start()
    harness.welcome(limits={C.B_LIMIT_MAX_MSG_BODY_BYTES: 4})
    harness.session.direct(Harness.PEER, "far too long")
    assert "body is 12 bytes" in harness.ops("error")[0]["message"]


def test_incoming_direct_notice_is_reported_as_direct(ready):
    """A NOTICE addressed to us is a direct message, not room content."""
    ready.feed(C.T_NOTICE, src=Harness.PEER, dst=Harness.ME, body="psst", nick="bob")
    event = ready.ops("direct")[0]
    assert event["src"] == Harness.PEER.hex()
    assert event["nick"] == "bob"
    assert event["body"] == "psst"
    assert "room" not in event
    assert ready.ops("chat") == []


def test_incoming_direct_is_sanitised(ready):
    """A direct message is hub-forwarded text and is cleaned before display."""
    ready.feed(C.T_NOTICE, src=Harness.PEER, dst=Harness.ME, body="a\nb")
    assert ready.ops("direct")[0]["body"] == "ab"


def test_room_notice_is_not_mistaken_for_a_direct_message(ready):
    """A NOTICE with a room and no K_DST stays room content."""
    ready.feed(C.T_NOTICE, src=Harness.PEER, room="#x", body="hi")
    assert ready.ops("direct") == []
    assert ready.ops("chat")[0]["kind"] == "notice"
