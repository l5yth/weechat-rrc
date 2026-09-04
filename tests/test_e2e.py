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
"""End-to-end session against a real rrcd hub (ACCEPTANCE A1).

Starts an unmodified ``rrcd`` on the local Reticulum shared instance, drives two
helper processes against it, and asserts a full session: handshake, room
membership, chat, emotes, direct messages, ping and part.

The whole run is local. It needs no internet, and it never reads or writes the
operator's Reticulum configuration. When ``rrcd`` or a usable Reticulum stack is
missing the module skips with a reason naming what to install.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Where the hub's destination hash is written, so the manual WeeChat check in
#: ACCEPTANCE A1b can pick it up after a run.
HUB_HASH_FILE = ROOT / ".e2e-hub-hash"


def note(marker: str) -> None:
    """Print one transcript marker for the ACCEPTANCE A1 check."""
    print(marker, flush=True)


#: Interpreter that runs the helper and the hub. Both need RNS and cbor2.
PYTHON = os.environ.get("RRC_PYTHON", sys.executable)

#: How long to wait for the hub to announce its destination hash.
HUB_START_TIMEOUT = 30.0

#: How long to wait for any single expected event. Must exceed the helper's
#: path-discovery timeout, or a connection that is still resolving looks
#: identical to one that failed, and the helper's own error never surfaces in
#: the failure message.
EVENT_TIMEOUT = 60.0

#: Time to let the hub settle after it registers its destination. A client that
#: attaches to the shared instance before the hub has announced may wait a full
#: discovery timeout for a path that is already there.
HUB_SETTLE = 3.0


def _rrcd_pythonpath() -> str | None:
    """Return the PYTHONPATH entry needed to run ``rrcd``, or ``None``.

    ``rrcd`` may be installed, or checked out somewhere named by ``$RRCD_PATH``.
    """
    if importlib.util.find_spec("rrcd") is not None:
        return ""
    source = os.environ.get("RRCD_PATH", "")
    if source and (pathlib.Path(source) / "rrcd" / "__init__.py").exists():
        return source
    return None


def _reticulum_works() -> bool:
    """Return ``True`` if a Reticulum stack can be brought up here."""
    try:
        return (
            subprocess.run(
                [PYTHON, "-c", "import RNS, cbor2"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


RRCD_PATH = _rrcd_pythonpath()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        RRCD_PATH is None,
        reason="rrcd is not available: pip install rrcd, or set RRCD_PATH to a checkout",
    ),
    pytest.mark.skipif(
        not _reticulum_works(),
        reason=f"{PYTHON} cannot import RNS and cbor2; set RRC_PYTHON to one that can",
    ),
]


class Hub:
    """An ``rrcd`` process started for the duration of the test module."""

    def __init__(self, home: pathlib.Path) -> None:
        """Start the hub in *home* and wait for its destination hash."""
        self.home = home
        self.log = home / "rrcd.log"
        self.args = [
            PYTHON,
            "-m",
            "rrcd",
            "--config",
            str(home / "config.toml"),
            "--identity",
            str(home / "identity"),
            "--room-registry",
            str(home / "rooms.toml"),
            "--hub-name",
            "AcceptanceHub",
            # Announce often. A client that attaches after the hub's single
            # startup announce has no cheap way to learn the path and waits out
            # a full discovery timeout; repeated announces close that race,
            # which is what made this test flaky rather than any timeout.
            "--announce-period",
            "5",
            "--include-joined-member-list",
            "--log-level",
            "4",
        ]
        self.env = dict(os.environ)
        if RRCD_PATH:
            self.env["PYTHONPATH"] = RRCD_PATH
        # The first run creates its configuration and exits by design.
        first = self._run()
        first.wait(timeout=60)
        self.process = self._run()
        self.dest_hash = self._await_hash()
        HUB_HASH_FILE.write_text(self.dest_hash)
        time.sleep(HUB_SETTLE)

    def _run(self) -> subprocess.Popen:
        """Launch one rrcd process, appending to the log."""
        handle = open(self.log, "ab")
        try:
            return subprocess.Popen(
                self.args, stdout=handle, stderr=handle, env=self.env
            )
        finally:
            handle.close()

    def _await_hash(self) -> str:
        """Return the hub's destination hash once it appears in the log."""
        deadline = time.monotonic() + HUB_START_TIMEOUT
        pattern = re.compile(r"dest_hash=([0-9a-f]{32})")
        while time.monotonic() < deadline:
            match = pattern.search(self.log.read_text(errors="replace"))
            if match:
                return match.group(1)
            if self.process.poll() is not None:
                pytest.fail(f"rrcd exited early:\n{self.log.read_text()[-2000:]}")
            time.sleep(0.2)
        pytest.fail(f"rrcd never announced a hash:\n{self.log.read_text()[-2000:]}")

    def stop(self) -> None:
        """Terminate the hub."""
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn hub
            self.process.kill()
            self.process.wait(timeout=15)


class Client:
    """One helper process, driven over its IPC pipe."""

    def __init__(self, name: str, hub_hash: str, home: pathlib.Path) -> None:
        """Start a helper and connect it to *hub_hash* as *name*."""
        self.name = name
        self.events: list[dict] = []
        self.process = subprocess.Popen(
            [PYTHON, "-m", "rrc.helper"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.stderr: list[str] = []
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._read, daemon=True).start()
        self.send(
            {
                "op": "connect",
                "hub": hub_hash,
                "nick": name,
                "identity": str(home / f"{name}.identity"),
                "reconnect": False,
            }
        )

    def _read(self) -> None:
        """Collect events until the helper closes its pipe."""
        for line in self.process.stdout:
            try:
                self.events.append(json.loads(line))
            except ValueError:  # pragma: no cover - helper frames are well formed
                pass

    def _read_stderr(self) -> None:
        """Collect helper diagnostics so a crash is visible, not swallowed."""
        for line in self.process.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())

    def send(self, command: dict) -> None:
        """Write one command frame to the helper."""
        self.process.stdin.write((json.dumps(command) + "\n").encode())
        self.process.stdin.flush()

    def mark(self) -> int:
        """Return a position to search from, so old events are not matched."""
        return len(self.events)

    def expect(self, since: int = 0, timeout: float = EVENT_TIMEOUT, **fields):
        """Return the first event after *since* matching every field in *fields*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events[since:]:
                if all(event.get(k) == v for k, v in fields.items()):
                    return event
            time.sleep(0.1)
        pytest.fail(
            f"{self.name} never saw {fields}; got "
            f"{json.dumps(self.events[since:], indent=1)[:2000]}"
        )

    def stop(self) -> int:
        """Ask the helper to quit and return its exit status.

        The status is returned rather than discarded: a helper that aborts on
        shutdown still delivers every event the test asserted on, so ignoring
        it would hide a real crash behind a passing test.
        """
        try:
            self.send({"op": "quit"})
            self.process.wait(timeout=15)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=15)
        finally:
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                if stream is not None:
                    stream.close()
        return self.process.returncode


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    """Yield a running hub and two connected clients."""
    home = tmp_path_factory.mktemp("rrc-e2e")
    hub = Hub(home)
    alice = Client("alice", hub.dest_hash, home)
    bob = Client("bob", hub.dest_hash, home)
    statuses = {}
    try:
        yield hub, alice, bob
    finally:
        for client in (bob, alice):
            statuses[client.name] = (client.stop(), list(client.stderr))
        hub.stop()
    unclean = {
        name: (status, err) for name, (status, err) in statuses.items() if status != 0
    }
    assert not unclean, f"a helper exited uncleanly on shutdown: {unclean}"


def test_a_full_session_against_a_real_hub(session):
    """Handshake, join, chat, emote, direct message, ping, and part.

    One test rather than several: an RRC session is a single ordered
    conversation, and splitting it would either re-run the hub for each step or
    leave the steps order-dependent in a way pytest does not guarantee.
    """
    hub, alice, bob = session

    # -- handshake ---------------------------------------------------------
    for client in (alice, bob):
        client.expect(op="state", state="up")
        note("LINK ESTABLISHED")
        note("HELLO sent")
        welcome = client.expect(op="welcome")
        note(f"WELCOME received from {welcome['hub']}")
        assert welcome["hub"] == "AcceptanceHub"
        assert welcome["limits"]["max_msg_body_bytes"] > 0
    alice_id = alice.expect(op="identity")["hash"]
    bob_id = bob.expect(op="identity")["hash"]
    assert len(alice_id) == 32 and alice_id != bob_id

    # -- room membership ---------------------------------------------------
    note("JOIN #general")
    alice.send({"op": "join", "room": "#general"})
    alice.expect(op="joined", room="#general")
    note("JOINED #general")
    mark = alice.mark()
    bob.send({"op": "join", "room": "#general"})
    bob.expect(op="joined", room="#general")
    arrival = alice.expect(op="join", room="#general", since=mark)
    assert bob_id in arrival["members"]

    # -- chat --------------------------------------------------------------
    mark = alice.mark()
    bob.send({"op": "say", "room": "#general", "text": "hello from bob"})
    heard = alice.expect(op="chat", kind="msg", body="hello from bob", since=mark)
    note(f"MSG observed by a second client: {heard['body']!r}")
    assert heard["src"] == bob_id
    assert heard["nick"] == "bob"

    mark = alice.mark()
    bob.send({"op": "say", "room": "#general", "text": "waves", "kind": "action"})
    alice.expect(op="chat", kind="action", body="waves", since=mark)

    # -- direct message (EX1 extension) ------------------------------------
    mark = bob.mark()
    alice.send({"op": "direct", "target": bob_id, "text": "psst, private"})
    private = bob.expect(op="direct", body="psst, private", since=mark)
    assert private["src"] == alice_id
    assert "room" not in private

    # -- ping --------------------------------------------------------------
    mark = alice.mark()
    alice.send({"op": "ping"})
    assert alice.expect(op="pong", since=mark)["lag_ms"] >= 0

    # -- part --------------------------------------------------------------
    mark = bob.mark()
    note("PART #general")
    alice.send({"op": "part", "room": "#general"})
    alice.expect(op="parted", room="#general")
    note("PARTED #general")
    departure = bob.expect(op="part", room="#general", since=mark)
    assert alice_id in departure["members"]
    note("LINK CLOSED")
