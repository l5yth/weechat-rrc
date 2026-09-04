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
"""Envelope encoding conformance and forward-compatibility (ACCEPTANCE A3b, A4).

The ``conformance`` tests reproduce the worked examples in 3-RRC §Examples and
check the byte budgets that keep an envelope inside the Reticulum MTU. The
``unknown``/``forward`` tests exercise 3-RRC §Forward Compatibility Rules: every
one of them asserts that hostile or unexpected input is *ignored*, never raised.
"""

from __future__ import annotations

import cbor2
import pytest

from rrc.helper import constants as C
from rrc.helper import envelope as E

# --------------------------------------------------------------------------
# Encoding conformance (ACCEPTANCE A3b)
# --------------------------------------------------------------------------


def test_conformance_minimum_envelope_is_43_bytes(src_hash, msg_id):
    """A minimal envelope is 43 bytes (3-RRC §Minimum Envelope Overhead).

    Only message types 0-23 hit this figure: CBOR packs those into the type
    byte, while 24-255 need a second byte. See ACCEPTANCE A3c.
    """
    raw = E.encode(C.T_HELLO, msg_id=msg_id, ts=1737849600000, src=src_hash)
    assert len(raw) == 43


def test_conformance_large_type_costs_one_extra_byte(src_hash, msg_id):
    """Types above 23 encode one byte larger, as CBOR requires."""
    raw = E.encode(C.T_PING, msg_id=msg_id, ts=1737849600000, src=src_hash)
    assert len(raw) == 44


def test_conformance_msg_example(src_hash, msg_id):
    """Reproduce the 3-RRC §Examples MSG envelope, ~73 bytes."""
    raw = E.encode(
        C.T_MSG,
        msg_id=msg_id,
        ts=1737849600000,
        src=src_hash,
        room="#lobby",
        body="Hello, world!",
        nick="alice",
    )
    assert len(raw) == 73
    decoded = cbor2.loads(raw)
    assert decoded[C.K_V] == 1
    assert decoded[C.K_T] == 20
    assert decoded[C.K_ROOM] == "#lobby"
    assert decoded[C.K_BODY] == "Hello, world!"
    assert decoded[C.K_NICK] == "alice"


def test_conformance_join_example(src_hash):
    """Reproduce the 3-RRC §Examples JOIN envelope, ~60 bytes, no body."""
    raw = E.encode(
        C.T_JOIN,
        msg_id=bytes.fromhex("b45c2a8913d76f1e"),
        ts=1737849610000,
        src=src_hash,
        room="#general",
        nick="alice",
    )
    assert len(raw) == 60
    # 3-RRC: "For JOIN, the body is empty or omitted."
    assert C.K_BODY not in cbor2.loads(raw)


def test_conformance_worst_case_msg_fits_the_mtu(src_hash, msg_id):
    """The 3-RRC §Practical Size Budget worst case fits in 500 bytes."""
    raw = E.encode(
        C.T_MSG,
        msg_id=msg_id,
        ts=1737849600000,
        src=src_hash,
        room="#" + "r" * 63,  # 64 bytes
        nick="n" * 32,  # 32 bytes
        body="b" * 350,  # 350 bytes
    )
    assert len(raw) == 499
    assert len(raw) <= C.RETICULUM_MTU


def test_conformance_all_keys_are_unsigned_integers(src_hash, msg_id):
    """3-RRC forbids string keys; rrcd rejects envelopes that use them."""
    raw = E.encode(C.T_MSG, msg_id=msg_id, src=src_hash, room="#x", body="y", nick="z")
    for key in cbor2.loads(raw):
        assert isinstance(key, int) and not isinstance(key, bool)
        assert key >= 0


def test_conformance_absent_fields_are_omitted_not_null(src_hash, msg_id):
    """Optional fields are omitted entirely, keeping the envelope minimal."""
    decoded = cbor2.loads(E.encode(C.T_PING, msg_id=msg_id, src=src_hash))
    assert set(decoded) == {C.K_V, C.K_T, C.K_ID, C.K_TS, C.K_SRC}


def test_conformance_utf8_room_name_is_measured_in_bytes(src_hash, msg_id):
    """A 2-character, 6-byte room name costs 6 bytes of payload, not 2."""
    ascii_raw = E.encode(C.T_JOIN, msg_id=msg_id, src=src_hash, room="ab")
    wide_raw = E.encode(C.T_JOIN, msg_id=msg_id, src=src_hash, room="学习")
    assert len("学习".encode("utf-8")) == 6
    assert len(wide_raw) - len(ascii_raw) == 4


def test_encode_generates_defaults(src_hash):
    """Message ID and timestamp are generated when not supplied."""
    decoded = cbor2.loads(E.encode(C.T_PING, src=src_hash))
    assert len(decoded[C.K_ID]) == C.MSG_ID_BYTES
    assert decoded[C.K_TS] > 1_700_000_000_000


def test_new_msg_id_is_eight_random_bytes():
    """3-RRC §Field 2 requires 8 bytes from a cryptographic source."""
    ids = {E.new_msg_id() for _ in range(64)}
    assert all(len(i) == C.MSG_ID_BYTES for i in ids)
    assert len(ids) == 64  # collisions here would mean a broken CSPRNG


def test_now_ms_is_milliseconds():
    """The timestamp helper returns epoch milliseconds, not seconds."""
    assert E.now_ms() > 1_700_000_000_000


# --------------------------------------------------------------------------
# Encoding is strict about fixed widths
# --------------------------------------------------------------------------


def test_encode_rejects_short_message_id(src_hash):
    """A message ID that is not 8 bytes is a malformed frame."""
    with pytest.raises(ValueError, match="message id must be 8 bytes"):
        E.encode(C.T_PING, msg_id=b"\x00", src=src_hash)


def test_encode_rejects_wrong_length_identity():
    """A sender identity that is not 16 bytes is a malformed frame."""
    with pytest.raises(ValueError, match="sender identity must be 16 bytes"):
        E.encode(C.T_PING, src=b"\x00" * 8)


def test_encode_rejects_wrong_length_destination(src_hash):
    """EX1 requires a full 16-byte identity hash in K_DST."""
    with pytest.raises(ValueError, match="destination identity must be 16 bytes"):
        E.encode(C.T_NOTICE, src=src_hash, dst=b"\x00" * 4, body="hi")


def test_encode_rejects_room_and_destination_together(src_hash, hub_hash):
    """EX1: K_ROOM must be omitted when K_DST is present."""
    with pytest.raises(ValueError, match="room must be omitted"):
        E.encode(C.T_NOTICE, src=src_hash, dst=hub_hash, room="#x", body="hi")


def test_encode_direct_notice_has_dst_and_no_room(src_hash, hub_hash):
    """A well-formed direct NOTICE carries K_DST and omits K_ROOM."""
    decoded = cbor2.loads(E.encode(C.T_NOTICE, src=src_hash, dst=hub_hash, body="hi"))
    assert decoded[C.K_DST] == hub_hash
    assert C.K_ROOM not in decoded


# --------------------------------------------------------------------------
# Decoding round-trip
# --------------------------------------------------------------------------


def test_decode_round_trip_preserves_every_field(src_hash, hub_hash, msg_id):
    """Encoding then decoding returns the same envelope contents."""
    raw = E.encode(
        C.T_MSG,
        msg_id=msg_id,
        ts=1737849600000,
        src=src_hash,
        room="#lobby",
        body="hello",
        nick="alice",
    )
    env = E.decode(raw)
    assert env is not None
    assert env.version == 1
    assert env.type == C.T_MSG
    assert env.msg_id == msg_id
    assert env.ts == 1737849600000
    assert env.src == src_hash
    assert env.room == "#lobby"
    assert env.body == "hello"
    assert env.nick == "alice"
    assert env.dst is None


def test_decode_preserves_direct_destination(src_hash, hub_hash):
    """A direct NOTICE round-trips its destination hash."""
    env = E.decode(E.encode(C.T_NOTICE, src=src_hash, dst=hub_hash, body="hi"))
    assert env is not None and env.dst == hub_hash


def test_envelope_type_name(src_hash):
    """Envelopes expose a readable type name for logging."""
    env = E.decode(E.encode(C.T_ACTION, src=src_hash, room="#x", body="waves"))
    assert env is not None and env.type_name == "ACTION"


def test_envelope_type_name_falls_back_for_unnamed_type(src_hash):
    """An envelope constructed with an unnamed type still renders."""
    env = E.Envelope(type=99, msg_id=b"\x00" * 8, ts=0, src=src_hash)
    assert env.type_name == "TYPE_99"


def test_decode_accepts_bytearray_fields(src_hash, msg_id):
    """Byte-string fields decoded as bytearray are normalised to bytes."""
    raw = cbor2.dumps(
        {
            C.K_V: 1,
            C.K_T: C.T_PING,
            C.K_ID: bytearray(msg_id),
            C.K_TS: 1,
            C.K_SRC: bytearray(src_hash),
        }
    )
    env = E.decode(raw)
    assert env is not None
    assert isinstance(env.msg_id, bytes) and isinstance(env.src, bytes)


# --------------------------------------------------------------------------
# Forward compatibility: unknown input is ignored, never fatal (ACCEPTANCE A4)
# --------------------------------------------------------------------------


def _frame(overrides=None):
    """Build a raw CBOR frame from a valid base, applying *overrides*.

    Overrides arrive as a plain dict rather than keyword arguments because RRC
    envelope keys are integers, which Python cannot express as ``**kwargs``.
    """
    fields = {
        C.K_V: 1,
        C.K_T: C.T_MSG,
        C.K_ID: b"\x01" * 8,
        C.K_TS: 1737849600000,
        C.K_SRC: b"\x02" * 16,
        C.K_BODY: "hi",
    }
    fields.update(overrides or {})
    return cbor2.dumps(fields)


def test_forward_unknown_envelope_keys_are_ignored():
    """3-RRC: unknown envelope keys must be ignored, not rejected."""
    env = E.decode(_frame({50: "extension", 63: b"\xff"}))
    assert env is not None and env.body == "hi"


def test_forward_string_keys_are_ignored():
    """String keys are not valid RRC; they are dropped as unknown keys."""
    raw = cbor2.dumps(
        {
            C.K_V: 1,
            C.K_T: C.T_MSG,
            C.K_ID: b"\x01" * 8,
            C.K_TS: 1,
            C.K_SRC: b"\x02" * 16,
            "body": "smuggled",
        }
    )
    env = E.decode(raw)
    assert env is not None and env.body is None


def test_forward_unknown_message_type_is_discarded():
    """2-RRC §Extensibility: unknown message types must be ignored."""
    assert E.decode(_frame({C.K_T: 64})) is None


def test_forward_resource_envelope_is_discarded():
    """SPEC.md D7: type 50 is not implemented, so it is ignored."""
    assert E.decode(_frame({C.K_T: C.T_RESOURCE_ENVELOPE})) is None


@pytest.mark.parametrize("version", [0, 2, 255])
def test_forward_other_protocol_versions_are_discarded(version):
    """3-RRC §Field 0: only version 1 is accepted."""
    assert E.decode(_frame({C.K_V: version})) is None


def test_forward_boolean_version_is_not_mistaken_for_one():
    """CBOR ``true`` must not pass as the integer version 1."""
    assert E.decode(_frame({C.K_V: True})) is None


def test_forward_negative_values_are_rejected():
    """Envelope integers are unsigned; a negative value is malformed."""
    assert E.decode(_frame({C.K_TS: -1})) is None


@pytest.mark.parametrize("missing", [C.K_V, C.K_T, C.K_ID, C.K_TS, C.K_SRC])
def test_forward_missing_mandatory_field_is_discarded(missing):
    """A frame missing any mandatory envelope field is ignored."""
    fields = cbor2.loads(_frame())
    del fields[missing]
    assert E.decode(cbor2.dumps(fields)) is None


@pytest.mark.parametrize(
    "key,value",
    [
        (C.K_T, "twenty"),
        (C.K_ID, "not-bytes"),
        (C.K_TS, "yesterday"),
        (C.K_SRC, 12345),
    ],
)
def test_forward_mistyped_mandatory_field_is_discarded(key, value):
    """A mandatory field of the wrong CBOR type is ignored."""
    assert E.decode(_frame({key: value})) is None


@pytest.mark.parametrize(
    "key,attr", [(C.K_ROOM, "room"), (C.K_NICK, "nick"), (C.K_DST, "dst")]
)
def test_forward_mistyped_advisory_field_is_treated_as_absent(key, attr):
    """An advisory field of the wrong type is dropped, not fatal."""
    env = E.decode(_frame({key: 12345}))
    assert env is not None and getattr(env, attr) is None


def test_forward_malformed_cbor_is_discarded():
    """Unparseable bytes are ignored without raising."""
    assert E.decode(b"\xff\xff\xff\xff") is None
    assert E.decode(b"") is None


@pytest.mark.parametrize("payload", [[1, 2, 3], "a string", 42, None])
def test_forward_non_map_frames_are_discarded(payload):
    """3-RRC: every RRC message is a CBOR map; anything else is ignored."""
    assert E.decode(cbor2.dumps(payload)) is None
