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
"""Hub limit parsing and enforcement (ACCEPTANCE C7).

Limits arrive under numeric keys 0-4 in the ``WELCOME`` body and are measured
in UTF-8 bytes. Both of those are easy to get subtly wrong, so they are pinned
here with multi-byte text rather than ASCII.
"""

from __future__ import annotations

import pytest

from rrc.helper import constants as C
from rrc.helper.limits import Limits

FULL = {
    C.B_LIMIT_MAX_NICK_BYTES: 32,
    C.B_LIMIT_MAX_ROOM_NAME_BYTES: 64,
    C.B_LIMIT_MAX_MSG_BODY_BYTES: 350,
    C.B_LIMIT_MAX_ROOMS_PER_SESSION: 16,
    C.B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE: 30,
}


def test_limits_read_from_numeric_keys():
    """All five limits are read from numeric keys 0-4 (EX1 §Hub Limits Map)."""
    limits = Limits.from_welcome_body({C.B_WELCOME_LIMITS: FULL})
    assert limits.max_nick_bytes == 32
    assert limits.max_room_name_bytes == 64
    assert limits.max_msg_body_bytes == 350
    assert limits.max_rooms_per_session == 16
    assert limits.rate_limit_msgs_per_minute == 30


def test_limits_expose_specification_names():
    """The limits render under the names the specification uses."""
    limits = Limits.from_welcome_body({C.B_WELCOME_LIMITS: FULL})
    assert limits.as_dict() == {
        "max_nick_bytes": 32,
        "max_room_name_bytes": 64,
        "max_msg_body_bytes": 350,
        "max_rooms_per_session": 16,
        "rate_limit_msgs_per_minute": 30,
    }


@pytest.mark.parametrize("body", [None, "text", 42, {}, {C.B_WELCOME_LIMITS: 7}])
def test_limits_absent_or_malformed_yield_no_limits(body):
    """A hub that advertises nothing usable leaves every limit unknown."""
    limits = Limits.from_welcome_body(body)
    assert limits.as_dict() == {}
    assert limits.max_msg_body_bytes is None


@pytest.mark.parametrize("value", [0, -1, "32", True, None, 1.5])
def test_limits_ignore_values_that_are_not_positive_integers(value):
    """A nonsensical limit is dropped rather than half-applied."""
    limits = Limits.from_welcome_body(
        {C.B_WELCOME_LIMITS: {C.B_LIMIT_MAX_NICK_BYTES: value}}
    )
    assert limits.max_nick_bytes is None


def test_limits_ignore_unknown_keys():
    """Unknown limit keys are ignored, per the forward-compatibility rules."""
    limits = Limits.from_welcome_body(
        {C.B_WELCOME_LIMITS: {C.B_LIMIT_MAX_NICK_BYTES: 32, 99: 1}}
    )
    assert limits.as_dict() == {"max_nick_bytes": 32}


@pytest.mark.parametrize("kind", ["nick", "room", "body"])
def test_check_passes_when_no_limit_is_known(kind):
    """With no advertised limit, nothing is refused locally."""
    assert Limits().check(kind, "x" * 10_000) is None


def test_check_allows_a_value_exactly_at_the_limit():
    """The limit is inclusive; a value of exactly N bytes is allowed."""
    limits = Limits(max_msg_body_bytes=350)
    assert limits.check("body", "x" * 350) is None
    assert limits.check("body", "x" * 351) is not None


def test_check_measures_utf8_bytes_not_characters():
    """A 2-character, 6-byte string is measured as 6 bytes."""
    assert len("学习".encode("utf-8")) == 6
    limits = Limits(max_room_name_bytes=5)
    complaint = limits.check("room", "学习")
    assert complaint is not None
    assert "6 bytes" in complaint


def test_check_names_the_limit_and_the_actual_size():
    """The complaint tells the user both numbers so they can shorten it."""
    complaint = Limits(max_nick_bytes=8).check("nick", "a" * 12)
    assert "12 bytes" in complaint and "8" in complaint
