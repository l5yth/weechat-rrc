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
"""Newline-delimited JSON framing for the WeeChat-to-helper pipe.

The WeeChat script and the helper process exchange one JSON object per line
(``SPEC.md`` D8). JSON rather than CBOR on this hop keeps ``rrc.py`` free of
third-party imports and leaves the stream readable when something goes wrong;
the CBOR wire format exists only between the helper and the hub.

JSON has no byte-string type, so binary values — identity hashes and message
identifiers — travel as lowercase hex via :func:`to_hex` and :func:`from_hex`.

Frames arrive from a pipe in arbitrary chunks, so :class:`FrameReader`
reassembles them. It never raises on malformed input: a corrupt frame is
counted and dropped, because a parser that throws would take down the session
loop on the other side.
"""

from __future__ import annotations

import json
from typing import Any

#: Largest frame accepted before the reader assumes the stream is corrupt and
#: resynchronises. Room messages are MTU-bound, so this is generous.
MAX_FRAME_BYTES = 1 << 20


def to_hex(raw: bytes | None) -> str | None:
    """Return *raw* as lowercase hex, or ``None`` if it is ``None``."""
    return None if raw is None else raw.hex()


def from_hex(text: str | None) -> bytes | None:
    """Return *text* decoded from hex, or ``None`` if it is absent or invalid.

    Invalid hex yields ``None`` rather than raising: the value came off a pipe
    and a malformed field must not abort the frame that carried it.
    """
    if not text:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def encode_frame(obj: dict[str, Any]) -> bytes:
    """Serialise *obj* as one frame: compact JSON followed by a newline.

    Args:
        obj: A JSON-serialisable mapping. Binary values must already be hex.

    Returns:
        UTF-8 bytes ready to write to the pipe.
    """
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


class FrameReader:
    """Reassembles newline-delimited JSON frames from arbitrary pipe chunks.

    Attributes:
        dropped: Count of frames discarded as malformed or oversized. Exposed
            so the helper can report a broken stream instead of failing mute.
    """

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        """Initialise an empty reader.

        Args:
            max_frame_bytes: Size at which an unterminated frame is abandoned.
        """
        self._buffer = bytearray()
        self._max = max_frame_bytes
        self._resyncing = False
        self.dropped = 0

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        """Consume *chunk* and return every complete frame it completed.

        Args:
            chunk: Bytes just read from the pipe. May contain no frame, part
                of one, several, or the tail of one plus the head of another.

        Returns:
            The decoded frames, in order. Malformed frames are dropped and
            counted in :attr:`dropped` rather than raised.
        """
        if self._resyncing:
            newline = chunk.find(b"\n")
            if newline < 0:
                # Still inside the oversized frame; keep discarding.
                return []
            chunk = chunk[newline + 1 :]
            self._resyncing = False

        self._buffer.extend(chunk)
        frames: list[dict[str, Any]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            frame = self._decode(line)
            if frame is not None:
                frames.append(frame)

        if len(self._buffer) > self._max:
            # No newline in sight and the buffer is unreasonable: treat the
            # stream as corrupt, drop what we have, and skip to the next
            # newline rather than growing without bound.
            self._buffer.clear()
            self._resyncing = True
            self.dropped += 1
        return frames

    def _decode(self, line: bytes) -> dict[str, Any] | None:
        """Decode one complete line, or return ``None`` if it is unusable."""
        if not line.strip():
            return None  # keepalive or stray newline, not an error
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            self.dropped += 1
            return None
        if not isinstance(obj, dict):
            self.dropped += 1
            return None
        return obj
