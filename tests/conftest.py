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
"""Shared pytest fixtures for the weechat-rrc test suite."""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

# The repository root, so both ``rrc_helper`` and the top-level ``rrc.py``
# WeeChat script are importable without installing the package.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def src_hash() -> bytes:
    """Return a deterministic 16-byte sender identity hash."""
    return bytes.fromhex("9c7e0102030405060708090a0b0c4a2f")


@pytest.fixture
def hub_hash() -> bytes:
    """Return a deterministic 16-byte hub identity hash."""
    return bytes.fromhex("1f8a0102030405060708090a0b0cb3c5")


@pytest.fixture
def msg_id() -> bytes:
    """Return the 8-byte message identifier from the 3-RRC MSG example."""
    return bytes.fromhex("7a3f8e1245c9a16d")


class Harness:
    """A live :class:`RRCSession` with its sent frames and events captured.

    Wraps the state machine so tests can drive it without a Link: outbound
    envelopes land in :attr:`sent` already decoded, and events destined for
    WeeChat land in :attr:`events`.
    """

    #: Identity hash used for the local client in tests.
    ME = bytes.fromhex("9c7e0102030405060708090a0b0c4a2f")
    #: Identity hash used for the hub in tests.
    HUB = bytes.fromhex("1f8a0102030405060708090a0b0cb3c5")
    #: Identity hash used for a second client in tests.
    PEER = bytes.fromhex("aabb0102030405060708090a0b0cccdd")

    def __init__(self, nick=None):
        """Create a session that records everything it sends and emits."""
        from rrc_helper.session import RRCSession

        self.raw = []
        self.events = []
        self.now = 1000.0
        self.session = RRCSession(
            self.ME,
            send=self.raw.append,
            emit=self.events.append,
            nick=nick,
            clock=lambda: self.now,
        )

    def feed(self, msg_type, **kwargs):
        """Encode an envelope as if from the hub and hand it to the session."""
        from rrc_helper import envelope as E

        kwargs.setdefault("src", self.HUB)
        self.session.on_frame(E.encode(msg_type, **kwargs))

    def welcome(self, caps=(1, 2), limits=None, hub="TestHub"):
        """Deliver a WELCOME, optionally advertising caps and limits."""
        from rrc_helper import constants as C

        body = {C.B_WELCOME_HUB: hub, C.B_WELCOME_VER: "0.1.0"}
        body[C.B_WELCOME_CAPS] = {cap: True for cap in caps}
        if limits:
            body[C.B_WELCOME_LIMITS] = limits
        self.feed(C.T_WELCOME, body=body)

    @property
    def sent(self):
        """Return every outbound envelope, decoded."""
        from rrc_helper import envelope as E

        return [E.decode(raw) for raw in self.raw]

    @property
    def last(self):
        """Return the most recently sent envelope, decoded."""
        return self.sent[-1]

    def ops(self, op):
        """Return every emitted event whose ``op`` matches."""
        return [e for e in self.events if e.get("op") == op]


@pytest.fixture
def harness():
    """Return a session harness with no nickname set."""
    return Harness()


@pytest.fixture
def ready():
    """Return a harness that has already completed the HELLO/WELCOME exchange."""
    h = Harness(nick="afri")
    h.session.start()
    h.welcome()
    return h


class FakeProcess:
    """A stand-in for the helper subprocess, backed by real pipes.

    Real pipes are used for stdout and stderr so the script's non-blocking
    ``os.read`` path is exercised rather than mocked away.
    """

    def __init__(self):
        """Open pipes and start with an empty command log."""
        self.written = []
        self.killed = False
        self.closed = False
        self._out_r, self._out_w = os.pipe()
        self._err_r, self._err_w = os.pipe()
        self.stdout = os.fdopen(self._out_r, "rb", buffering=0)
        self.stderr = os.fdopen(self._err_r, "rb", buffering=0)
        self.stdin = self

    def write(self, data):
        """Record a command frame written by the script."""
        if self.closed:
            raise ValueError("stdin is closed")
        self.written.append(json.loads(data))
        return len(data)

    def flush(self):
        """Accept the script's flush; nothing is buffered here."""

    def close(self):
        """Mark stdin closed, as the real pipe would be."""
        self.closed = True

    def emit(self, *events):
        """Push events onto the helper's stdout pipe."""
        for event in events:
            os.write(self._out_w, (json.dumps(event) + "\n").encode())

    def emit_stderr(self, text):
        """Push a diagnostic line onto the helper's stderr pipe."""
        os.write(self._err_w, text.encode())

    def wait(self, timeout=None):
        """Report immediate, clean exit."""
        return 0

    def kill(self):
        """Record that the process was killed."""
        self.killed = True

    def cleanup(self):
        """Close every descriptor so no warning escapes the test."""
        for closer in (self.stdout.close, self.stderr.close):
            try:
                closer()
            except OSError:  # pragma: no cover - already closed
                pass
        for fd in (self._out_w, self._err_w):
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - already closed
                pass


@pytest.fixture
def wee(monkeypatch):
    """Import the WeeChat script against the fake API and yield both.

    Returns a ``(weechat, rrc)`` pair. The script module is reloaded per test so
    module-level state cannot leak between them.
    """
    from tests import fake_weechat

    fake_weechat.state.reset()
    monkeypatch.setitem(sys.modules, "weechat", fake_weechat)
    sys.modules.pop("rrc", None)
    import rrc

    rrc.connections.clear()
    yield fake_weechat, rrc
    rrc.connections.clear()
    sys.modules.pop("rrc", None)


@pytest.fixture
def connected(wee, monkeypatch):
    """Return ``(weechat, rrc, connection, process)`` with a started helper."""
    fake_weechat, rrc = wee
    process = FakeProcess()
    monkeypatch.setattr(rrc, "find_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(rrc.subprocess, "Popen", lambda *a, **k: process)
    rrc.rrc_command_cb("", "", "connect 28c7c1a68c735693aa8e6b8193ed44b2 -nick afri")
    connection = rrc.connections["28c7c1a6"]
    yield fake_weechat, rrc, connection, process
    process.cleanup()
