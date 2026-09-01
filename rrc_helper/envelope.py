# SPDX-FileCopyrightText: 2026 l5yth & contributors
# SPDX-License-Identifier: Apache-2.0
#
# Copyright © 2026 l5yth & contributors
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
"""RRC envelope encoding and decoding, per 3-RRC.

Every RRC message is a CBOR map with unsigned-integer keys. This module is the
only place that shape is constructed or interpreted, so wire conformance is
verifiable in one file.

Two rules from 3-RRC drive the design:

* **Encoding is strict.** :func:`encode` refuses to build an envelope that
  violates a fixed field width, because emitting a malformed frame makes this
  client the broken party.
* **Decoding is permissive.** 3-RRC §Forward Compatibility Rules requires that
  unknown keys, unknown message types, and unparseable frames be *ignored*, not
  rejected loudly. :func:`decode` therefore returns ``None`` for anything that
  must be dropped and never raises on hostile input.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import cbor2

from . import constants as C


@dataclass(frozen=True)
class Envelope:
    """A decoded RRC envelope with unknown keys already discarded.

    Attributes mirror 3-RRC §Envelope Structure. ``room``, ``body``, ``nick``
    and ``dst`` are ``None`` when the corresponding key was absent, or when it
    was present with a type the specification does not allow — an advisory
    field of the wrong type is treated as absent rather than as a fatal error.
    """

    type: int
    """Message type, guaranteed to be a member of :data:`constants.KNOWN_TYPES`."""

    msg_id: bytes
    """Sender-chosen message identifier."""

    ts: int
    """Sender's timestamp in milliseconds. Advisory; clocks do not agree."""

    src: bytes
    """Sender's Reticulum identity hash. The only authoritative identifier."""

    room: str | None = None
    """Room this message applies to, if any."""

    body: Any = None
    """Message-type-dependent payload, passed through uninterpreted."""

    nick: str | None = None
    """Advisory nickname. Never trust this as identity (1-RRC §Identity)."""

    dst: bytes | None = None
    """Direct-message destination hash (``EX1``), when present."""

    version: int = C.RRC_VERSION
    """Protocol version; always :data:`constants.RRC_VERSION` after decoding."""

    @property
    def type_name(self) -> str:
        """Return the human-readable name of this envelope's message type."""
        return C.TYPE_NAMES.get(self.type, f"TYPE_{self.type}")


def new_msg_id() -> bytes:
    """Return a fresh 8-byte message identifier from a cryptographic CSPRNG.

    3-RRC §Field 2 requires a cryptographically secure random source and gives
    ``os.urandom(8)`` as the reference.
    """
    return os.urandom(C.MSG_ID_BYTES)


def now_ms() -> int:
    """Return the current time in milliseconds since the Unix epoch."""
    return int(time.time() * 1000)


def encode(
    msg_type: int,
    *,
    src: bytes,
    msg_id: bytes | None = None,
    ts: int | None = None,
    room: str | None = None,
    body: Any = None,
    nick: str | None = None,
    dst: bytes | None = None,
) -> bytes:
    """Encode an RRC envelope to CBOR bytes.

    Optional fields are omitted entirely when ``None``, rather than encoded as
    CBOR null, so that a minimal envelope stays at the 43 bytes 3-RRC specifies.

    Args:
        msg_type: One of the ``T_*`` message types in :mod:`.constants`.
        src: The sender's 16-byte Reticulum identity hash.
        msg_id: 8-byte identifier; a fresh one is generated when omitted.
        ts: Milliseconds since the epoch; the current time when omitted.
        room: Room name, omitted for messages that do not apply to a room.
        body: Message-specific payload.
        nick: Advisory nickname.
        dst: Direct-message destination hash (``EX1``).

    Returns:
        The CBOR-encoded envelope.

    Raises:
        ValueError: If a fixed-width field is the wrong length, or if both
            ``room`` and ``dst`` are supplied. ``EX1`` §Direct NOTICE Delivery
            requires ``K_ROOM`` to be omitted when ``K_DST`` is present; hubs
            reject envelopes carrying both rather than guessing.
    """
    if msg_id is None:
        msg_id = new_msg_id()
    if ts is None:
        ts = now_ms()
    if len(msg_id) != C.MSG_ID_BYTES:
        raise ValueError(
            f"message id must be {C.MSG_ID_BYTES} bytes, got {len(msg_id)}"
        )
    if len(src) != C.IDENTITY_HASH_BYTES:
        raise ValueError(
            f"sender identity must be {C.IDENTITY_HASH_BYTES} bytes, got {len(src)}"
        )
    if dst is not None:
        if len(dst) != C.IDENTITY_HASH_BYTES:
            raise ValueError(
                f"destination identity must be {C.IDENTITY_HASH_BYTES} bytes, "
                f"got {len(dst)}"
            )
        if room is not None:
            raise ValueError("EX1: room must be omitted when dst is present")

    envelope: dict[int, Any] = {
        C.K_V: C.RRC_VERSION,
        C.K_T: msg_type,
        C.K_ID: msg_id,
        C.K_TS: ts,
        C.K_SRC: src,
    }
    if room is not None:
        envelope[C.K_ROOM] = room
    if body is not None:
        envelope[C.K_BODY] = body
    if nick is not None:
        envelope[C.K_NICK] = nick
    if dst is not None:
        envelope[C.K_DST] = dst
    return cbor2.dumps(envelope)


def _uint(value: Any) -> int | None:
    """Return *value* as a non-negative int, or ``None`` if it is not one.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` in Python, so
    a CBOR ``true`` would otherwise pass as the integer 1 and let a malformed
    frame masquerade as a valid protocol version.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _text(value: Any) -> str | None:
    """Return *value* if it is a text string, else ``None``."""
    return value if isinstance(value, str) else None


def _blob(value: Any) -> bytes | None:
    """Return *value* if it is a byte string, else ``None``."""
    return value if isinstance(value, (bytes, bytearray)) else None


def decode(raw: bytes) -> Envelope | None:
    """Decode a CBOR frame into an :class:`Envelope`, or ``None`` to ignore it.

    Returns ``None`` — never raises — when the frame is unparseable, is not a
    CBOR map, declares a protocol version other than
    :data:`constants.RRC_VERSION`, omits or mistypes a mandatory envelope
    field, or carries a message type this client does not implement. Each of
    those is an "ignore it" case under 3-RRC §Error Handling on the Wire and
    §Forward Compatibility Rules.

    Args:
        raw: The bytes received over the Reticulum Link.

    Returns:
        The decoded envelope, or ``None`` if the frame must be dropped.
    """
    try:
        obj = cbor2.loads(raw)
    except Exception:
        # Any malformed frame is discarded silently; a hostile peer must not
        # be able to raise inside the session loop.
        return None
    if not isinstance(obj, dict):
        return None

    # Non-integer keys are "unknown keys" per 3-RRC §Canonical Encoding Rules
    # and are dropped before any field is read.
    fields = {
        k: v for k, v in obj.items() if isinstance(k, int) and not isinstance(k, bool)
    }

    if _uint(fields.get(C.K_V)) != C.RRC_VERSION:
        return None
    msg_type = _uint(fields.get(C.K_T))
    if msg_type is None or msg_type not in C.KNOWN_TYPES:
        return None
    msg_id = _blob(fields.get(C.K_ID))
    ts = _uint(fields.get(C.K_TS))
    src = _blob(fields.get(C.K_SRC))
    if msg_id is None or ts is None or src is None:
        return None

    return Envelope(
        type=msg_type,
        msg_id=bytes(msg_id),
        ts=ts,
        src=bytes(src),
        room=_text(fields.get(C.K_ROOM)),
        body=fields.get(C.K_BODY),
        nick=_text(fields.get(C.K_NICK)),
        dst=_blob(fields.get(C.K_DST)),
    )
