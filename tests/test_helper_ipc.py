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
"""IPC framing tests.

The reader sits between two processes and must survive whatever the pipe hands
it, so most of these tests feed it broken input and assert it keeps going.
"""

from __future__ import annotations

import json

import pytest

from rrc_helper import ipc

# --------------------------------------------------------------------------
# Hex conversion at the JSON boundary
# --------------------------------------------------------------------------


def test_to_hex_and_back():
    """Binary values survive a round trip through the JSON boundary."""
    raw = bytes.fromhex("9c7e0102030405060708090a0b0c4a2f")
    assert ipc.to_hex(raw) == "9c7e0102030405060708090a0b0c4a2f"
    assert ipc.from_hex(ipc.to_hex(raw)) == raw


def test_to_hex_passes_through_none():
    """An absent binary field stays absent rather than becoming ``""``."""
    assert ipc.to_hex(None) is None


@pytest.mark.parametrize("bad", [None, "", "zz", "abc", "not hex"])
def test_from_hex_returns_none_for_unusable_input(bad):
    """Malformed hex yields None instead of raising into the session loop."""
    assert ipc.from_hex(bad) is None


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------


def test_encode_frame_is_one_line_of_compact_json():
    """A frame is exactly one newline-terminated line."""
    raw = ipc.encode_frame({"op": "ping", "n": 1})
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert b" " not in raw  # compact separators
    assert json.loads(raw) == {"op": "ping", "n": 1}


def test_encode_frame_escapes_embedded_newlines():
    """A newline inside a value cannot forge a frame boundary."""
    raw = ipc.encode_frame({"body": "line one\nline two"})
    assert raw.count(b"\n") == 1
    assert json.loads(raw)["body"] == "line one\nline two"


def test_encode_frame_keeps_unicode_readable():
    """Non-ASCII text is not escaped, keeping the stream inspectable."""
    raw = ipc.encode_frame({"body": "学习"})
    assert "学习".encode("utf-8") in raw


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_reader_returns_a_whole_frame():
    """A complete frame in one chunk decodes immediately."""
    assert ipc.FrameReader().feed(b'{"op":"hello"}\n') == [{"op": "hello"}]


def test_reader_reassembles_a_frame_split_across_chunks():
    """A frame split mid-token is buffered until its newline arrives."""
    reader = ipc.FrameReader()
    assert reader.feed(b'{"op":"he') == []
    assert reader.feed(b'llo","n":1}') == []
    assert reader.feed(b"\n") == [{"op": "hello", "n": 1}]


def test_reader_returns_several_frames_from_one_chunk():
    """Multiple frames in a single read are returned in order."""
    frames = ipc.FrameReader().feed(b'{"a":1}\n{"b":2}\n{"c":3}\n')
    assert frames == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_reader_holds_a_trailing_partial_frame():
    """A trailing fragment is kept for the next read, not misparsed."""
    reader = ipc.FrameReader()
    assert reader.feed(b'{"a":1}\n{"b":') == [{"a": 1}]
    assert reader.feed(b"2}\n") == [{"b": 2}]


def test_reader_ignores_blank_lines():
    """Stray newlines are not errors and are not counted as drops."""
    reader = ipc.FrameReader()
    assert reader.feed(b'\n\n{"a":1}\n\n') == [{"a": 1}]
    assert reader.dropped == 0


def test_reader_drops_malformed_json_and_continues():
    """A corrupt frame is counted and skipped; later frames still arrive."""
    reader = ipc.FrameReader()
    assert reader.feed(b'garbage\n{"a":1}\n') == [{"a": 1}]
    assert reader.dropped == 1


def test_reader_drops_invalid_utf8_and_continues():
    """Undecodable bytes are dropped rather than raising."""
    reader = ipc.FrameReader()
    assert reader.feed(b'\xff\xfe\n{"a":1}\n') == [{"a": 1}]
    assert reader.dropped == 1


@pytest.mark.parametrize("payload", [b"[1,2,3]", b'"a string"', b"42", b"null"])
def test_reader_drops_frames_that_are_not_objects(payload):
    """Every frame is a JSON object; other top-level types are dropped."""
    reader = ipc.FrameReader()
    assert reader.feed(payload + b"\n") == []
    assert reader.dropped == 1


def test_reader_resynchronises_after_an_oversized_frame():
    """An unterminated flood is abandoned, then the stream recovers."""
    reader = ipc.FrameReader(max_frame_bytes=64)
    assert reader.feed(b"x" * 512) == []
    assert reader.dropped == 1
    # Still mid-garbage: no newline yet, so nothing is returned.
    assert reader.feed(b"y" * 128) == []
    # The next newline ends the junk and normal framing resumes.
    assert reader.feed(b'trailing junk\n{"a":1}\n') == [{"a": 1}]


def test_reader_does_not_grow_without_bound():
    """The buffer is bounded even when the peer never sends a newline."""
    reader = ipc.FrameReader(max_frame_bytes=1024)
    for _ in range(64):
        reader.feed(b"z" * 512)
    assert len(reader._buffer) <= 1024


def test_reader_round_trips_encoded_frames():
    """Frames written by ``encode_frame`` are read back unchanged."""
    sent = [{"op": "join", "room": "#general"}, {"op": "msg", "body": "hi"}]
    stream = b"".join(ipc.encode_frame(f) for f in sent)
    assert ipc.FrameReader().feed(stream) == sent
