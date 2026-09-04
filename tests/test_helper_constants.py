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
"""Assert the protocol numbers match 3-RRC exactly (ACCEPTANCE A3a).

3-RRC states that message type values "are fixed. They are not suggestions."
These tests therefore compare against literals rather than against the module's
own names: importing a constant and asserting it equals itself would pass even
if the value drifted.
"""

from __future__ import annotations

from rrc.helper import constants as C


def test_protocol_version_is_one():
    """3-RRC §Field 0 mandates protocol version 1."""
    assert C.RRC_VERSION == 1


def test_envelope_keys_match_spec():
    """Envelope keys 0-8 match 3-RRC §Envelope Structure and EX1."""
    assert (C.K_V, C.K_T, C.K_ID, C.K_TS, C.K_SRC) == (0, 1, 2, 3, 4)
    assert (C.K_ROOM, C.K_BODY, C.K_NICK) == (5, 6, 7)
    assert C.K_DST == 8


def test_message_types_match_spec():
    """Message types match the 3-RRC §Message Type Assignments table."""
    assert (C.T_HELLO, C.T_WELCOME) == (1, 2)
    assert (C.T_JOIN, C.T_JOINED, C.T_PART, C.T_PARTED) == (10, 11, 12, 13)
    assert (C.T_MSG, C.T_NOTICE, C.T_ACTION) == (20, 21, 22)
    assert (C.T_PING, C.T_PONG) == (30, 31)
    assert C.T_ERROR == 40
    assert C.T_RESOURCE_ENVELOPE == 50


def test_fixed_field_widths_match_spec():
    """Message ID is 8 bytes and identity hashes are 16 (3-RRC §Fixed Fields)."""
    assert C.MSG_ID_BYTES == 8
    assert C.IDENTITY_HASH_BYTES == 16


def test_hub_destination_naming():
    """1-RRC §Transport names the hub destination ``rrc.hub``."""
    assert C.HUB_APP_NAME == "rrc"
    assert C.HUB_ASPECTS == ("hub",)
    assert ".".join((C.HUB_APP_NAME, *C.HUB_ASPECTS)) == "rrc.hub"


def test_known_types_covers_every_core_type():
    """Every core message type is understood; the type-50 extension is not."""
    assert C.KNOWN_TYPES == {1, 2, 10, 11, 12, 13, 20, 21, 22, 30, 31, 40}
    # SPEC.md D7: resource transfer is out of scope, so it must be ignored.
    assert C.T_RESOURCE_ENVELOPE not in C.KNOWN_TYPES


def test_type_names_cover_known_types_and_the_extension():
    """Every known type, plus type 50, has a display name."""
    assert set(C.TYPE_NAMES) == C.KNOWN_TYPES | {C.T_RESOURCE_ENVELOPE}
    assert C.TYPE_NAMES[C.T_MSG] == "MSG"


def test_body_and_limit_keys_match_ex1():
    """HELLO/WELCOME body keys and the limits map match EX1."""
    assert (C.B_HELLO_NAME, C.B_HELLO_VER, C.B_HELLO_CAPS) == (0, 1, 2)
    assert (C.B_WELCOME_HUB, C.B_WELCOME_VER) == (0, 1)
    assert (C.B_WELCOME_CAPS, C.B_WELCOME_LIMITS) == (2, 3)
    assert C.B_LIMIT_MAX_NICK_BYTES == 0
    assert C.B_LIMIT_MAX_ROOM_NAME_BYTES == 1
    assert C.B_LIMIT_MAX_MSG_BODY_BYTES == 2
    assert C.B_LIMIT_MAX_ROOMS_PER_SESSION == 3
    assert C.B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE == 4
    assert set(C.LIMIT_NAMES) == {0, 1, 2, 3, 4}
    assert C.LIMIT_NAMES[0] == "max_nick_bytes"


def test_capability_keys_match_ex1():
    """Capability keys match EX1's numeric assignments."""
    assert C.CAP_RESOURCE_ENVELOPE == 0
    assert C.CAP_ACTION == 1
    assert C.CAP_DIRECT_NOTICE == 2


def test_mtu_constants():
    """MTU figures match 3-RRC §Reticulum MTU Constraints."""
    assert C.RETICULUM_MTU == 500
    assert C.MAX_ENVELOPE_BYTES == 465
