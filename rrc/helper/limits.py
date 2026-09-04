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
"""Hub-advertised limits from the ``WELCOME`` body.

3-RRC describes these limits with string names, but its own worked example and
the reference hub both put them under numeric keys, which ``EX1`` §Hub Limits
Map fixes as 0-4. The numeric form is what appears on the wire.

Limits are advisory to the client and enforced by the hub: the hub rejects an
oversized message whether or not the client checked first. Checking locally
turns a silent rejection into a message the user can act on before sending.

Every length here is a count of **UTF-8 bytes**, not characters. A two-character
string can occupy six bytes, and it is the byte count the hub measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import constants as C


@dataclass(frozen=True)
class Limits:
    """Operational limits a hub advertises in its ``WELCOME``.

    Every field is ``None`` when the hub did not advertise it, which means
    "no client-side limit known" rather than "unlimited".
    """

    max_nick_bytes: int | None = None
    """Longest nickname the hub accepts, in UTF-8 bytes."""

    max_room_name_bytes: int | None = None
    """Longest room name the hub accepts, in UTF-8 bytes."""

    max_msg_body_bytes: int | None = None
    """Largest message body the hub accepts, in UTF-8 bytes."""

    max_rooms_per_session: int | None = None
    """Most rooms one session may be joined to at once."""

    rate_limit_msgs_per_minute: int | None = None
    """Messages per minute before the hub starts refusing them."""

    @classmethod
    def from_welcome_body(cls, body: object) -> "Limits":
        """Build limits from a decoded ``WELCOME`` body.

        Args:
            body: The ``WELCOME`` body, which may be any CBOR value. Anything
                that is not a map, or that carries non-integer values, yields
                empty limits rather than an error: 2-RRC requires clients to
                work when no field is present.

        Returns:
            The advertised limits, with unknown or malformed entries omitted.
        """
        if not isinstance(body, dict):
            return cls()
        raw = body.get(C.B_WELCOME_LIMITS)
        if not isinstance(raw, dict):
            return cls()
        return cls(
            max_nick_bytes=_positive_int(raw.get(C.B_LIMIT_MAX_NICK_BYTES)),
            max_room_name_bytes=_positive_int(raw.get(C.B_LIMIT_MAX_ROOM_NAME_BYTES)),
            max_msg_body_bytes=_positive_int(raw.get(C.B_LIMIT_MAX_MSG_BODY_BYTES)),
            max_rooms_per_session=_positive_int(
                raw.get(C.B_LIMIT_MAX_ROOMS_PER_SESSION)
            ),
            rate_limit_msgs_per_minute=_positive_int(
                raw.get(C.B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE)
            ),
        )

    def check(self, kind: str, text: str) -> str | None:
        """Return a complaint if *text* exceeds the limit for *kind*.

        Args:
            kind: One of ``"nick"``, ``"room"`` or ``"body"``.
            text: The value the user is about to send.

        Returns:
            A user-facing message naming the limit and the actual size, or
            ``None`` when the value fits or no limit is known.
        """
        limit = {
            "nick": self.max_nick_bytes,
            "room": self.max_room_name_bytes,
            "body": self.max_msg_body_bytes,
        }[kind]
        if limit is None:
            return None
        size = len(text.encode("utf-8"))
        if size <= limit:
            return None
        return (
            f"{kind} is {size} bytes, but this hub allows {limit}; "
            f"the hub would reject it"
        )

    def as_dict(self) -> dict[str, int]:
        """Return the advertised limits keyed by their specification names."""
        named = {
            C.B_LIMIT_MAX_NICK_BYTES: self.max_nick_bytes,
            C.B_LIMIT_MAX_ROOM_NAME_BYTES: self.max_room_name_bytes,
            C.B_LIMIT_MAX_MSG_BODY_BYTES: self.max_msg_body_bytes,
            C.B_LIMIT_MAX_ROOMS_PER_SESSION: self.max_rooms_per_session,
            C.B_LIMIT_RATE_LIMIT_MSGS_PER_MINUTE: self.rate_limit_msgs_per_minute,
        }
        return {
            C.LIMIT_NAMES[key]: value
            for key, value in named.items()
            if value is not None
        }


def _positive_int(value: object) -> int | None:
    """Return *value* if it is a positive integer, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
