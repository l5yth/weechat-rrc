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
"""Helper process wiring tests.

Drives the command dispatcher and run loop with a fake Link, so the IPC
contract can be checked without Reticulum or a hub.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from rrc_helper import __main__ as helper_mod
from rrc_helper import identity as identity_store
from rrc_helper import ipc
from rrc_helper import link as link_mod
from rrc_helper.__main__ import Helper

HUB = "28c7c1a68c735693aa8e6b8193ed44b2"
PEER = "aabb0102030405060708090a0b0cccdd"


class FakeIdentity:
    """Stand-in for a loaded Reticulum identity."""

    hash = bytes.fromhex("9c7e0102030405060708090a0b0c4a2f")


class FakeHubLink:
    """Stand-in for :class:`rrc_helper.link.HubLink`."""

    created = []
    fail_open = None

    def __init__(self, dest_hash, identity, on_up, on_frame, on_down):
        """Record the wiring and expose the callbacks to the test."""
        self.dest_hash = dest_hash
        self.identity = identity
        self.on_up = on_up
        self.on_frame = on_frame
        self.on_down = on_down
        self.sent = []
        self.closed = False
        self.open_error = None
        FakeHubLink.created.append(self)

    def open(self, *args, **kwargs):
        """Succeed, or raise the error the test planted."""
        if FakeHubLink.fail_open:
            raise FakeHubLink.fail_open
        if self.open_error:
            raise self.open_error

    def send(self, data):
        """Record an outbound envelope."""
        self.sent.append(data)

    def close(self):
        """Record teardown."""
        self.closed = True


@pytest.fixture
def helper(monkeypatch, tmp_path):
    """Return a helper writing to a buffer, with Reticulum faked out."""
    FakeHubLink.created = []
    FakeHubLink.fail_open = None
    monkeypatch.setattr(helper_mod.link_mod, "HubLink", FakeHubLink)
    monkeypatch.setattr(identity_store, "load_or_create", lambda path: FakeIdentity())
    monkeypatch.setattr(identity_store, "default_path", lambda: tmp_path / "identity")
    scheduled = []
    out = io.BytesIO()
    obj = Helper(out, schedule=lambda d, fn: scheduled.append((d, fn)))
    obj.scheduled = scheduled
    return obj


def events(helper_obj):
    """Return every event the helper has emitted, decoded."""
    return [json.loads(line) for line in helper_obj.out.getvalue().splitlines()]


def ops(helper_obj, op):
    """Return every emitted event whose ``op`` matches."""
    return [e for e in events(helper_obj) if e.get("op") == op]


# -- command dispatch ------------------------------------------------------


def test_unknown_command_is_reported(helper):
    """An unrecognised command names itself so the mismatch is obvious."""
    helper.handle({"op": "nonsense"})
    assert "unknown command 'nonsense'" in ops(helper, "error")[0]["message"]


def test_commands_before_connecting_are_refused(helper):
    """Every session command needs a session; none of them crash without one."""
    for cmd in ("join", "part", "say", "nick", "ping", "direct"):
        helper.handle({"op": cmd, "room": "#x", "target": PEER})
    assert len(ops(helper, "error")) == 6
    assert all("not connected" in e["message"] for e in ops(helper, "error"))


def test_connect_reports_the_identity_and_opens_a_link(helper):
    """Connecting publishes our identity hash and starts a Link."""
    helper.handle({"op": "connect", "hub": HUB})
    assert ops(helper, "identity")[0]["hash"] == FakeIdentity.hash.hex()
    assert ops(helper, "state")[0]["state"] == "connecting"
    assert FakeHubLink.created[0].dest_hash == bytes.fromhex(HUB)


def test_connect_rejects_a_malformed_hub_address(helper):
    """A bad hash is a user error and is reported, not raised."""
    helper.handle({"op": "connect", "hub": "nonsense"})
    assert "32 hex characters" in ops(helper, "error")[0]["message"]


def test_connect_reports_an_unusable_identity_file(helper, monkeypatch, tmp_path):
    """An exposed or corrupt identity file stops the connection cleanly."""
    monkeypatch.setattr(
        identity_store,
        "load_or_create",
        lambda path: (_ for _ in ()).throw(identity_store.IdentityError("bad key")),
    )
    helper.handle({"op": "connect", "hub": HUB})
    assert ops(helper, "error")[0]["message"] == "bad key"


def test_link_failure_reports_and_schedules_a_retry(helper):
    """A hub we cannot reach produces an error and a scheduled reconnect."""
    FakeHubLink.fail_open = link_mod.LinkError("no path to hub")
    helper.handle({"op": "connect", "hub": HUB})
    assert "no path to hub" in ops(helper, "error")[-1]["message"]
    assert ops(helper, "reconnect")[-1]["seconds"] == 10.0
    assert helper.scheduled[0][0] == 10.0


def test_repeated_failures_back_off(helper):
    """Each failed attempt waits longer than the last."""
    FakeHubLink.fail_open = link_mod.LinkError("no path")
    helper.handle({"op": "connect", "hub": HUB})
    helper._open_link()
    helper._open_link()
    delays = [e["seconds"] for e in ops(helper, "reconnect")]
    assert delays == [10.0, 20.0, 40.0]


def test_disconnect_closes_and_stops_reconnecting(helper):
    """An explicit disconnect must not be undone by the backoff timer."""
    helper.handle({"op": "connect", "hub": HUB})
    helper.handle({"op": "disconnect"})
    assert FakeHubLink.created[0].closed
    assert helper.reconnect is False
    helper._schedule_retry()
    assert helper.scheduled == []


# -- session commands ------------------------------------------------------


@pytest.fixture
def connected(helper):
    """Return a helper with an established, welcomed session."""
    from rrc_helper import constants as C
    from rrc_helper import envelope as E

    helper.handle({"op": "connect", "hub": HUB, "nick": "afri"})
    helper._on_up()
    helper.session.on_frame(
        E.encode(
            C.T_WELCOME,
            src=bytes.fromhex(PEER),
            body={C.B_WELCOME_HUB: "TestHub", C.B_WELCOME_CAPS: {2: True}},
        )
    )
    return helper


def test_autojoin_waits_for_welcome(helper):
    """Rooms are joined after WELCOME, never on HELLO alone.

    2-RRC lets a hub answer anything sent before it has accepted the session
    with an error instead of acting on it; rrcd's router does exactly that.
    """
    from rrc_helper import constants as C
    from rrc_helper import envelope as E

    helper.handle({"op": "connect", "hub": HUB, "autojoin": ["#general", 42]})
    link = FakeHubLink.created[0]
    assert link.sent == []

    helper._on_up()
    assert ops(helper, "state")[-1]["state"] == "up"
    # Only HELLO so far: joining now could be refused by the hub.
    assert len(link.sent) == 1
    assert E.decode(link.sent[0]).type == C.T_HELLO

    helper.session.on_frame(E.encode(C.T_WELCOME, src=bytes.fromhex(PEER), body={}))
    # One JOIN; the non-string autojoin entry was discarded.
    assert len(link.sent) == 2
    assert E.decode(link.sent[1]).type == C.T_JOIN
    assert E.decode(link.sent[1]).room == "#general"


def test_autojoin_after_welcome_without_a_session_is_safe(helper):
    """A disconnect racing the WELCOME callback must not raise."""
    helper.handle({"op": "connect", "hub": HUB, "autojoin": ["#general"]})
    helper.session = None
    helper._join_autojoin()


def test_say_join_part_reach_the_link(connected):
    """The ordinary chat commands produce envelopes on the Link."""
    before = len(FakeHubLink.created[0].sent)
    connected.handle({"op": "join", "room": "#general"})
    connected.handle({"op": "say", "room": "#general", "text": "hi"})
    connected.handle(
        {"op": "say", "room": "#general", "text": "waves", "kind": "action"}
    )
    connected.handle({"op": "part", "room": "#general"})
    assert len(FakeHubLink.created[0].sent) == before + 4


def test_unknown_message_kind_is_reported(connected):
    """A bad ``kind`` is rejected rather than silently sent as a MSG."""
    connected.handle({"op": "say", "room": "#x", "text": "hi", "kind": "shout"})
    assert "unknown message kind" in ops(connected, "error")[-1]["message"]


def test_direct_requires_a_target_hash(connected):
    """A direct message with no usable target is refused."""
    connected.handle({"op": "direct", "text": "hi"})
    assert "needs a target identity hash" in ops(connected, "error")[-1]["message"]


def test_direct_reaches_the_link_when_supported(connected):
    """With the capability advertised, a direct message is sent."""
    before = len(FakeHubLink.created[0].sent)
    connected.handle({"op": "direct", "target": PEER, "text": "psst"})
    assert len(FakeHubLink.created[0].sent) == before + 1


def test_nick_and_ping_reach_the_session(connected):
    """Nick changes and pings are accepted once connected."""
    connected.handle({"op": "nick", "nick": "newnick"})
    connected.handle({"op": "ping"})
    assert ops(connected, "nick")[-1]["nick"] == "newnick"


def test_send_without_a_link_is_reported(connected):
    """Losing the Link mid-command reports rather than crashing."""
    connected.hub = None
    connected.handle({"op": "say", "room": "#x", "text": "hi"})
    assert "not connected" in ops(connected, "error")[-1]["message"]


def test_send_failure_is_reported(connected):
    """A Link that refuses a packet surfaces the reason."""

    def boom(data):
        raise link_mod.LinkError("link went away")

    FakeHubLink.created[0].send = boom
    connected.handle({"op": "say", "room": "#x", "text": "hi"})
    assert "link went away" in ops(connected, "error")[-1]["message"]


def test_link_down_resets_and_schedules_a_retry(connected):
    """A dropped Link clears the session and queues a reconnect."""
    connected._on_down("link timed out")
    assert connected.hub is None
    assert connected.session.rooms == set()
    assert ops(connected, "reconnect")[-1]["seconds"] == 10.0


def test_disconnect_before_connecting_is_safe(helper):
    """Disconnecting with no Link open reports down without raising."""
    helper.handle({"op": "disconnect"})
    assert ops(helper, "state")[-1]["reason"] == "disconnected"
    assert helper.reconnect is False


def test_link_down_without_a_session_is_safe(helper):
    """A teardown callback racing a failed connect must not raise."""
    helper._on_down("link timed out")
    assert helper.hub is None


def test_default_timer_runs_the_callback(helper):
    """The real scheduler is a daemon timer, so a stall cannot wedge exit."""
    import threading

    fired = threading.Event()
    timer = Helper._timer(0.01, fired.set)
    assert timer.daemon
    assert fired.wait(2.0)


def test_broken_pipe_stops_the_helper(helper):
    """When WeeChat goes away, the helper stops instead of looping on errors."""

    class Broken:
        def write(self, data):
            raise BrokenPipeError()

    helper.out = Broken()
    helper.emit({"op": "test"})
    assert helper._running is False


# -- run loop --------------------------------------------------------------


def test_run_processes_commands_then_exits_on_eof(helper):
    """Commands are executed in order and EOF ends the loop."""
    stream = io.BytesIO(
        ipc.encode_frame({"op": "connect", "hub": HUB})
        + ipc.encode_frame({"op": "join", "room": "#general"})
    )
    assert helper.run(stream) == 0
    assert ops(helper, "identity")


def test_read_commands_never_blocks_for_a_full_buffer(helper):
    """Commands must be read with ``read1``, not ``read``.

    On a pipe, ``read(n)`` blocks until it has all *n* bytes or the writer
    closes. Using it would stall every command until WeeChat exited, which is
    invisible in tests that use a pre-filled buffer and fatal in production.
    """

    class PipeLike:
        """A stream whose blocking ``read`` must never be called."""

        def __init__(self):
            self.chunks = [ipc.encode_frame({"op": "connect", "hub": HUB}), b""]

        def read1(self, size):
            return self.chunks.pop(0)

        def read(self, size):
            raise AssertionError("read() would block on a pipe; use read1()")

    helper.read_commands(PipeLike())
    kinds = []
    while not helper.events.empty():
        kinds.append(helper.events.get()[0])
    assert kinds == ["cmd", "eof"]


def test_read_commands_falls_back_to_read(helper):
    """A stream without ``read1``, such as a plain file, still works."""

    class FileLike:
        """A stream exposing only ``read``."""

        def __init__(self):
            self.chunks = [ipc.encode_frame({"op": "ping"}), b""]

        def read(self, size):
            return self.chunks.pop(0)

    helper.read_commands(FileLike())
    assert helper.events.qsize() == 2


def test_run_handles_link_events(helper):
    """Link callbacks are processed on the main loop, not the RNS thread."""
    from rrc_helper import constants as C
    from rrc_helper import envelope as E

    helper.handle({"op": "connect", "hub": HUB})
    link = FakeHubLink.created[0]
    link.on_up()
    link.on_frame(E.encode(C.T_WELCOME, src=bytes.fromhex(PEER), body={}))
    link.on_down("link timed out")
    helper.events.put(("eof", None))
    helper.run(io.BytesIO())
    assert ops(helper, "welcome")
    assert ops(helper, "state")[-1]["state"] == "down"


def test_run_ignores_frames_with_no_session(helper):
    """A stray inbound frame before connecting is discarded."""
    helper.events.put(("frame", b"\xff"))
    helper.events.put(("eof", None))
    assert helper.run(io.BytesIO()) == 0


def test_run_retries_on_schedule(helper):
    """The scheduled retry reopens the Link when it fires."""
    helper.handle({"op": "connect", "hub": HUB})
    helper.events.put(("retry", None))
    helper.events.put(("eof", None))
    helper.run(io.BytesIO())
    assert len(FakeHubLink.created) == 2


def test_quit_command_ends_the_loop(helper):
    """An explicit quit stops the helper and closes the Link."""
    stream = io.BytesIO(
        ipc.encode_frame({"op": "connect", "hub": HUB})
        + ipc.encode_frame({"op": "quit"})
    )
    assert helper.run(stream) == 0
    assert FakeHubLink.created[0].closed


# -- stdout protection -----------------------------------------------------


def test_claim_stdout_keeps_the_ipc_stream_clean():
    """Library output on stdout must not corrupt the IPC framing.

    Reticulum logs to stdout by default. After ``claim_stdout``, writes to file
    descriptor 1 land on stderr while the returned stream carries IPC frames.
    """
    ipc_r, ipc_w = os.pipe()
    err_r, err_w = os.pipe()
    saved_out, saved_err = os.dup(1), os.dup(2)
    try:
        os.dup2(ipc_w, 1)
        os.dup2(err_w, 2)
        stream = helper_mod.claim_stdout()
        stream.write(b'{"op":"frame"}\n')
        os.write(1, b"noisy library output\n")
        stream.close()
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
        os.close(ipc_w)
        os.close(err_w)
    assert os.read(ipc_r, 200) == b'{"op":"frame"}\n'
    assert os.read(err_r, 200) == b"noisy library output\n"
    os.close(ipc_r)
    os.close(err_r)


def test_main_wires_stdin_to_a_helper(monkeypatch):
    """``main`` attaches to Reticulum and runs until the pipe closes."""
    captured = {}

    class FakeReticulum:
        def __init__(self, *args, **kwargs):
            captured["attached"] = True

    monkeypatch.setattr(helper_mod.link_mod.RNS, "Reticulum", FakeReticulum)
    monkeypatch.setattr(helper_mod, "claim_stdout", lambda: io.BytesIO())
    monkeypatch.setattr(
        helper_mod.sys, "stdin", type("S", (), {"buffer": io.BytesIO()})()
    )
    assert helper_mod.main([]) == 0
    assert captured["attached"]
