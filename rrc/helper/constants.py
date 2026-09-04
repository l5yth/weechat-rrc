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
"""RRC protocol constants: envelope keys, message types, and body keys.

Every value here is fixed by the RRC specification v0.1.3, document 3-RRC
("Wire Encoding, Constants, and Numeric Assignments"), or by ``EX1-RRCD`` where
marked as an extension. 3-RRC states these numbers are "not suggestions", so
they are declared as literals and asserted as literals in the test suite: a
constant that drifts silently is a wire-incompatible client.
"""

#: RRC protocol version this client speaks. 3-RRC §Field 0 mandates ``1``;
#: envelopes carrying any other version are ignored.
RRC_VERSION = 1

#: Reticulum destination the hub listens on, as ``app_name`` plus aspects.
#: 1-RRC §Transport names ``rrc.hub`` as the convention.
HUB_APP_NAME = "rrc"
HUB_ASPECTS = ("hub",)

#: Fixed field widths from 3-RRC §Fixed Field Sizes.
MSG_ID_BYTES = 8
IDENTITY_HASH_BYTES = 16

#: Reticulum packet MTU, and the worst-case room for an RRC envelope inside it
#: (3-RRC §Reticulum MTU Constraints, assuming 32-byte addresses).
RETICULUM_MTU = 500
MAX_ENVELOPE_BYTES = 465

# --- Envelope keys (3-RRC §Envelope Structure) -----------------------------
K_V = 0
"""Protocol version."""
K_T = 1
"""Message type."""
K_ID = 2
"""Sender-chosen 8-byte message identifier."""
K_TS = 3
"""Timestamp, milliseconds since the Unix epoch. Advisory."""
K_SRC = 4
"""Sender's 16-byte Reticulum identity hash."""
K_ROOM = 5
"""Room name. Omitted when the message does not apply to a room."""
K_BODY = 6
"""Payload; its meaning depends on the message type."""
K_NICK = 7
"""Advisory nickname. Never identity."""
K_DST = 8
"""Direct destination identity hash. ``EX1`` extension, not core RRC."""

# --- Message types (3-RRC §Message Type Assignments) -----------------------
T_HELLO = 1
T_WELCOME = 2
T_JOIN = 10
T_JOINED = 11
T_PART = 12
T_PARTED = 13
T_MSG = 20
T_NOTICE = 21
T_ACTION = 22
T_PING = 30
T_PONG = 31
T_ERROR = 40
T_RESOURCE_ENVELOPE = 50
"""``EX1`` extension for over-MTU transfers. Not implemented (``SPEC.md`` D7),
so it is deliberately absent from :data:`KNOWN_TYPES` and inbound frames of
this type are ignored."""

#: Message types this client understands. 2-RRC §Extensibility requires that
#: anything else be ignored rather than rejected, so this set is the single
#: place that rule is enforced.
KNOWN_TYPES = frozenset(
    {
        T_HELLO,
        T_WELCOME,
        T_JOIN,
        T_JOINED,
        T_PART,
        T_PARTED,
        T_MSG,
        T_NOTICE,
        T_ACTION,
        T_PING,
        T_PONG,
        T_ERROR,
    }
)

#: Human-readable labels, for logging and for the ``/rrc status`` display.
TYPE_NAMES = {
    T_HELLO: "HELLO",
    T_WELCOME: "WELCOME",
    T_JOIN: "JOIN",
    T_JOINED: "JOINED",
    T_PART: "PART",
    T_PARTED: "PARTED",
    T_MSG: "MSG",
    T_NOTICE: "NOTICE",
    T_ACTION: "ACTION",
    T_PING: "PING",
    T_PONG: "PONG",
    T_ERROR: "ERROR",
    T_RESOURCE_ENVELOPE: "RESOURCE_ENVELOPE",
}

# --- HELLO body keys (3-RRC §HELLO body key assignments) -------------------
B_HELLO_NAME = 0
B_HELLO_VER = 1
B_HELLO_CAPS = 2

# --- WELCOME body keys (3-RRC §WELCOME body key assignments) ---------------
B_WELCOME_HUB = 0
B_WELCOME_VER = 1
B_WELCOME_CAPS = 2
B_WELCOME_LIMITS = 3

# --- Hub limit keys, inside the WELCOME limits map -------------------------
# 3-RRC's prose names these with string keys, but its own worked example and
# the rrcd reference implementation both use numeric keys; EX1 §Hub Limits Map
# fixes them as 0-4. The numeric form is authoritative on the wire.
B_LIMIT_MAX_NICK_BYTES = 0
B_LIMIT_MAX_ROOM_NAME_BYTES = 1
B_LIMIT_MAX_MSG_BODY_BYTES = 2
B_LIMIT_MAX_ROOMS_PER_SESSION = 3
B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE = 4

#: Names used for these limits in configuration and in user-facing messages.
LIMIT_NAMES = {
    B_LIMIT_MAX_NICK_BYTES: "max_nick_bytes",
    B_LIMIT_MAX_ROOM_NAME_BYTES: "max_room_name_bytes",
    B_LIMIT_MAX_MSG_BODY_BYTES: "max_msg_body_bytes",
    B_LIMIT_MAX_ROOMS_PER_SESSION: "max_rooms_per_session",
    B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE: "rate_limit_msgs_per_minute",
}

# --- Capability keys (EX1) -------------------------------------------------
CAP_RESOURCE_ENVELOPE = 0
"""Hub accepts over-MTU resource transfers. Not advertised by this client."""
CAP_ACTION = 1
"""Hub forwards ``ACTION`` as first-class room content."""
CAP_DIRECT_NOTICE = 2
"""Hub routes ``NOTICE`` addressed with :data:`K_DST`. Gates ``/msg``."""
