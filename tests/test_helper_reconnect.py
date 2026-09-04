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
"""Reconnect and autojoin behaviour (ACCEPTANCE C5).

RRC has no history: messages sent while a client is disconnected are gone.
These tests pin the reconnect policy and, just as importantly, assert that
nothing tries to invent a replay.
"""

from __future__ import annotations

import pytest

from rrc.helper import constants as C
from rrc.helper.link import Backoff, DEFAULT_BACKOFF_CAP


def test_backoff_doubles_from_the_base():
    """Successive attempts back off exponentially."""
    backoff = Backoff(base=10.0, cap=300.0)
    assert [backoff.next_delay() for _ in range(5)] == [10, 20, 40, 80, 160]


def test_backoff_stops_at_the_cap():
    """The delay never exceeds the configured ceiling."""
    backoff = Backoff(base=10.0, cap=300.0)
    delays = [backoff.next_delay() for _ in range(12)]
    assert max(delays) == 300.0
    assert delays[-1] == 300.0


def test_backoff_resets_after_a_successful_connection():
    """A successful connection returns to the initial delay."""
    backoff = Backoff(base=10.0, cap=300.0)
    backoff.next_delay()
    backoff.next_delay()
    backoff.reset()
    assert backoff.next_delay() == 10.0


def test_backoff_defaults_are_bounded():
    """The default ceiling keeps a dead hub from being polled forever."""
    backoff = Backoff()
    for _ in range(20):
        backoff.next_delay()
    assert backoff.next_delay() == DEFAULT_BACKOFF_CAP


def test_link_down_does_not_replay_anything(ready):
    """1-RRC: messages missed while disconnected are lost, by design."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[ready.ME])
    ready.feed(C.T_MSG, room="#general", src=ready.PEER, body="before")
    ready.session.on_link_down("link timed out")
    sent_before = len(ready.raw)
    events_before = len(ready.events)
    # A new session starts clean; nothing is re-requested or re-emitted.
    ready.session.start()
    assert len(ready.raw) == sent_before + 1
    assert ready.last.type == C.T_HELLO
    assert len(ready.events) == events_before


def test_rejoining_after_a_drop_starts_from_nothing(ready):
    """Membership is not remembered across a Link drop."""
    ready.session.join("#general")
    ready.feed(C.T_JOINED, room="#general", body=[ready.ME])
    ready.session.on_link_down()
    assert ready.session.rooms == set()
    # The room can be joined again because local state was cleared.
    ready.session.join("#general")
    assert ready.last.type == C.T_JOIN


def test_pending_pings_are_discarded_on_drop(ready):
    """A PONG arriving for a previous session yields no bogus lag figure."""
    ready.session.ping()
    token = ready.last.body
    ready.session.on_link_down()
    ready.feed(C.T_PONG, body=token)
    assert ready.ops("pong") == []
