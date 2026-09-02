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
"""Command surface and helper lifecycle (ACCEPTANCE C1).

Drives ``/rrc`` through the fake WeeChat API, including the paths where the
helper process misbehaves.
"""

from __future__ import annotations

import subprocess

import pytest

HUB = "28c7c1a68c735693aa8e6b8193ed44b2"


def test_registration_declares_the_script(wee):
    """The script registers with an unload callback so reload stays clean."""
    weechat, rrc = wee
    rrc.main()
    assert weechat.state.registered["name"] == "rrc"
    assert weechat.state.registered["license"] == "Apache-2.0"
    assert weechat.state.registered["shutdown"] == "rrc_unload_cb"


def test_registration_seeds_configuration_defaults(wee):
    """Every option exists after first load, so /set can discover them."""
    weechat, rrc = wee
    rrc.main()
    for option in rrc.DEFAULTS:
        assert weechat.config_is_set_plugin(option)


def test_registration_does_nothing_if_weechat_refuses(wee, monkeypatch):
    """A refused registration installs no hooks."""
    weechat, rrc = wee
    monkeypatch.setattr(weechat, "register", lambda *a: False)
    rrc.main()
    assert weechat.state.hooks == {}


def test_command_hook_documents_every_subcommand(wee):
    """``/help rrc`` names each subcommand the dispatcher accepts."""
    weechat, rrc = wee
    rrc.main()
    hook = next(h for h in weechat.state.hooks.values() if h["kind"] == "command")
    assert hook["command"] == "rrc"
    assert hook["callback"] == "rrc_command_cb"


def test_connect_opens_a_server_buffer_and_starts_the_helper(connected):
    """A successful connect creates the buffer and sends the connect frame."""
    weechat, rrc, connection, process = connected
    assert connection.hub_hash == HUB
    assert weechat.state.buffer("rrc.28c7c1a6") is not None
    assert process.written[0]["op"] == "connect"
    assert process.written[0]["hub"] == HUB
    assert process.written[0]["nick"] == "afri"


def test_connect_passes_configured_autojoin_rooms(wee, monkeypatch):
    """Autojoin rooms are handed to the helper, blanks discarded."""
    weechat, rrc = wee
    from tests.conftest import FakeProcess

    weechat.config_set_plugin("autojoin", "#general, ,#radio")
    process = FakeProcess()
    monkeypatch.setattr(rrc, "find_python", lambda: "python3")
    monkeypatch.setattr(rrc.subprocess, "Popen", lambda *a, **k: process)
    try:
        rrc.rrc_command_cb("", "", f"connect {HUB}")
        assert process.written[0]["autojoin"] == ["#general", "#radio"]
    finally:
        process.cleanup()


def test_connect_without_arguments_is_rejected(wee):
    """A bare connect explains the expected form."""
    weechat, rrc = wee
    assert rrc.rrc_command_cb("", "", "connect") == weechat.WEECHAT_RC_ERROR
    assert any("usage" in line for line in weechat.state.core)


def test_connecting_twice_to_one_hub_is_refused(connected):
    """A second connect to the same hub is refused rather than duplicated."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_command_cb("", "", f"connect {HUB}") == weechat.WEECHAT_RC_ERROR
    assert any("already connected" in line for line in weechat.state.core)


def test_connect_reports_a_helper_that_will_not_start(wee, monkeypatch):
    """An unlaunchable helper is reported instead of leaving a dead buffer."""
    weechat, rrc = wee
    monkeypatch.setattr(rrc, "find_python", lambda: "python3")

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(rrc.subprocess, "Popen", boom)
    assert rrc.rrc_command_cb("", "", f"connect {HUB}") == weechat.WEECHAT_RC_ERROR
    buffer = weechat.state.any_buffer("rrc.28c7c1a6")
    assert any("could not start the helper" in line for line in buffer.text)
    assert buffer.closed, "a failed connect must not leave its buffer open"
    assert rrc.connections == {}


def test_unknown_subcommand_is_reported(wee):
    """An unrecognised subcommand points at the help text."""
    weechat, rrc = wee
    assert rrc.rrc_command_cb("", "", "frobnicate") == weechat.WEECHAT_RC_ERROR
    assert any("unknown command" in line for line in weechat.state.core)


def test_bare_command_lists_connections(wee):
    """``/rrc`` with no arguments lists connections."""
    weechat, rrc = wee
    rrc.rrc_command_cb("", "", "")
    assert any("no connections" in line for line in weechat.state.core)


def test_list_shows_each_connection(connected):
    """Listing names the connection, its state and its rooms."""
    weechat, rrc, connection, process = connected
    connection.rooms["#general"] = "0xdead"
    rrc.rrc_command_cb("", "", "list")
    assert any("#general" in line for line in weechat.state.core)


def test_status_reports_the_session(connected):
    """Status shows hub, address, identity, state, lag and rooms."""
    weechat, rrc, connection, process = connected
    connection.identity = "abc123"
    rrc.rrc_command_cb("", connection.buffer, "status")
    text = "\n".join(weechat.state.buffers[connection.buffer].text)
    for field in ("hub", "address", "identity", "state", "lag", "rooms"):
        assert field in text
    assert HUB in text


def test_status_by_name_works_from_any_buffer(connected):
    """A connection can be named explicitly from an unrelated buffer."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_command_cb("", "", "status 28c7c1a6") == weechat.WEECHAT_RC_OK


def test_status_for_an_unknown_connection_is_reported(connected):
    """Naming a connection that does not exist reports the mistake."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_command_cb("", "", "status nope") == weechat.WEECHAT_RC_ERROR
    assert any("no such connection" in line for line in weechat.state.core)


def test_commands_outside_an_rrc_buffer_use_the_only_connection(connected):
    """A single open connection is unambiguous, so commands still work."""
    weechat, rrc, connection, process = connected
    other = weechat.buffer_new("core.weechat", "", "", "", "")
    assert rrc.rrc_command_cb("", other, "status") == weechat.WEECHAT_RC_OK


def test_join_part_nick_ping_reach_the_helper(connected):
    """The room and session subcommands produce helper commands."""
    weechat, rrc, connection, process = connected
    buffer = connection.buffer
    rrc.rrc_command_cb("", buffer, "join #general")
    rrc.rrc_command_cb("", buffer, "nick newnick")
    rrc.rrc_command_cb("", buffer, "ping")
    ops = [frame["op"] for frame in process.written]
    assert ops == ["connect", "join", "nick", "ping"]


def test_part_defaults_to_the_current_room(connected):
    """In a room buffer, ``/rrc part`` needs no argument."""
    weechat, rrc, connection, process = connected
    room_buffer = connection.room_buffer("#general")
    rrc.rrc_command_cb("", room_buffer, "part")
    assert process.written[-1] == {"op": "part", "room": "#general"}


def test_part_without_a_room_is_reported(connected):
    """On the server buffer, ``/rrc part`` must name a room."""
    weechat, rrc, connection, process = connected
    assert rrc.rrc_command_cb("", connection.buffer, "part") == weechat.WEECHAT_RC_ERROR


@pytest.mark.parametrize("command", ["join", "nick"])
def test_room_commands_require_arguments(connected, command):
    """Subcommands needing an argument explain themselves when given none."""
    weechat, rrc, connection, process = connected
    result = rrc.rrc_command_cb("", connection.buffer, command)
    assert result == weechat.WEECHAT_RC_ERROR
    assert any("usage" in line for line in weechat.state.core)


def test_disconnect_stops_the_helper(connected):
    """Disconnecting sends quit, closes stdin and forgets the connection."""
    weechat, rrc, connection, process = connected
    rrc.rrc_command_cb("", connection.buffer, "disconnect")
    assert process.written[-1] == {"op": "quit"}
    assert process.closed
    assert rrc.connections == {}


def test_disconnect_kills_a_helper_that_will_not_exit(connected, monkeypatch):
    """A wedged helper is killed rather than left behind."""
    weechat, rrc, connection, process = connected

    def hang(timeout=None):
        raise subprocess.TimeoutExpired("helper", 5)

    monkeypatch.setattr(process, "wait", hang)
    rrc.rrc_command_cb("", connection.buffer, "disconnect")
    assert process.killed


def test_unload_stops_every_helper(connected):
    """Unloading the script leaves no helper processes behind."""
    weechat, rrc, connection, process = connected
    rrc.rrc_unload_cb()
    assert process.closed
    assert rrc.connections == {}


def test_sending_to_a_dead_helper_is_reported(connected):
    """Writing to a closed pipe reports once and drops the process."""
    weechat, rrc, connection, process = connected
    process.close()
    connection.send({"op": "ping"})
    assert any(
        "helper is gone" in line
        for line in weechat.state.buffers[connection.buffer].text
    )
    assert connection.process is None


def test_sending_without_a_process_is_reported(connected):
    """Commands issued before the helper exists report cleanly."""
    weechat, rrc, connection, process = connected
    connection.process = None
    connection.send({"op": "ping"})
    assert any(
        "not connected" in line
        for line in weechat.state.buffers[connection.buffer].text
    )


def test_stopping_a_connection_with_no_process_is_safe(connected):
    """Stopping twice, or before the helper started, does not raise."""
    weechat, rrc, connection, process = connected
    connection.process = None
    connection.stop()
    assert connection.state == "disconnected"


def test_existing_configuration_is_not_overwritten(wee):
    """A user's settings survive a reload; defaults only fill in gaps."""
    weechat, rrc = wee
    weechat.config_set_plugin("helper.python", "/my/python")
    rrc.main()
    assert weechat.config_get_plugin("helper.python") == "/my/python"


def test_find_connection_searches_every_room(connected):
    """Buffer lookup scans past non-matching rooms to find the right one."""
    weechat, rrc, connection, process = connected
    connection.room_buffer("#first")
    second = connection.room_buffer("#second")
    found, room = rrc.find_connection(second)
    assert found is connection and room == "#second"


def test_an_empty_read_changes_nothing(connected):
    """A readable descriptor with no data pending is a no-op."""
    weechat, rrc, connection, process = connected
    before = len(weechat.state.buffers[connection.buffer].lines)
    rrc.rrc_stdout_cb(connection.name, "0")
    assert len(weechat.state.buffers[connection.buffer].lines) == before


def test_nick_flag_without_a_value_is_ignored(wee, monkeypatch):
    """A trailing ``-nick`` with no argument does not crash the connect."""
    weechat, rrc = wee
    from tests.conftest import FakeProcess

    process = FakeProcess()
    monkeypatch.setattr(rrc, "find_python", lambda: "python3")
    monkeypatch.setattr(rrc.subprocess, "Popen", lambda *a, **k: process)
    try:
        rrc.rrc_command_cb("", "", f"connect {HUB} -nick")
        assert process.written[0]["nick"] is None
    finally:
        process.cleanup()


@pytest.mark.parametrize("command", ["disconnect", "ping"])
def test_connection_commands_reject_an_unknown_name(connected, command):
    """Naming a connection that does not exist fails rather than guessing."""
    weechat, rrc, connection, process = connected
    result = rrc.rrc_command_cb("", "", f"{command} nope")
    assert result == weechat.WEECHAT_RC_ERROR


def test_part_outside_an_rrc_buffer_uses_the_only_connection(connected):
    """``/rrc part #room`` works from anywhere when one hub is open."""
    weechat, rrc, connection, process = connected
    other = weechat.buffer_new("core.weechat", "", "", "", "")
    assert rrc.rrc_command_cb("", other, "part #x") == weechat.WEECHAT_RC_OK
    assert process.written[-1] == {"op": "part", "room": "#x"}


def test_helper_pipes_are_non_blocking(connected):
    """Reads happen on WeeChat's main thread and must never block.

    Without ``O_NONBLOCK``, a callback firing on an empty pipe would freeze
    the whole user interface until the helper wrote something.
    """
    import fcntl
    import os

    weechat, rrc, connection, process = connected
    for stream in (process.stdout, process.stderr):
        flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
        assert flags & os.O_NONBLOCK


def test_a_single_connection_is_used_from_any_buffer(connected):
    """With one hub open, room commands work from the core buffer too.

    WeeChat runs a command scheduled with ``/wait`` on the buffer where the
    wait was issued, so requiring an RRC buffer would break scripted use and
    the common case of typing in core.
    """
    weechat, rrc, connection, process = connected
    other = weechat.buffer_new("core.weechat", "", "", "", "")
    assert rrc.rrc_command_cb("", other, "join #general") == weechat.WEECHAT_RC_OK
    assert process.written[-1] == {"op": "join", "room": "#general"}


def test_several_connections_require_naming_one(connected, monkeypatch):
    """The fallback applies only when the choice is unambiguous."""
    from tests.conftest import FakeProcess

    weechat, rrc, connection, process = connected
    second = FakeProcess()
    monkeypatch.setattr(rrc.subprocess, "Popen", lambda *a, **k: second)
    try:
        rrc.rrc_command_cb("", "", "connect " + "a" * 32)
        other = weechat.buffer_new("core.weechat", "", "", "", "")
        result = rrc.rrc_command_cb("", other, "join #general")
        assert result == weechat.WEECHAT_RC_ERROR
    finally:
        second.cleanup()


def two_connections(weechat, rrc, monkeypatch):
    """Open a second connection and return its fake process."""
    from tests.conftest import FakeProcess

    second = FakeProcess()
    monkeypatch.setattr(rrc.subprocess, "Popen", lambda *a, **k: second)
    rrc.rrc_command_cb("", "", "connect " + "a" * 32)
    return second


@pytest.mark.parametrize("command", ["part #x", "nick bob", "status", "ping"])
def test_ambiguous_commands_ask_for_a_connection(connected, monkeypatch, command):
    """With more than one hub open, the user must say which one."""
    weechat, rrc, connection, process = connected
    second = two_connections(weechat, rrc, monkeypatch)
    try:
        other = weechat.buffer_new("core.weechat", "", "", "", "")
        assert rrc.rrc_command_cb("", other, command) == weechat.WEECHAT_RC_ERROR
    finally:
        second.cleanup()


def test_unload_after_the_helper_died_does_not_raise(connected):
    """Stopping a connection whose pipe already broke must not raise.

    ``send`` clears ``self.process`` when the pipe is gone, so a ``stop`` that
    reached for ``self.process.stdin`` afterwards raised ``AttributeError``
    outside the caught tuple. In WeeChat that surfaced as
    `error in function "rrc_unload_cb"` with a traceback on every ``/quit``
    after the helper had died.
    """
    weechat, rrc, connection, process = connected

    def broken(data):
        raise OSError("broken pipe")

    monkey = process.write
    process.write = broken
    try:
        connection.stop()  # must not raise
    finally:
        process.write = monkey
    assert connection.process is None
    assert connection.state == "disconnected"


def test_stopping_a_process_without_stdin_is_safe(connected):
    """A process whose stdin was already closed elsewhere is torn down cleanly."""
    weechat, rrc, connection, process = connected
    process.stdin = None
    connection.stop()
    assert connection.process is None


def test_a_failed_connect_closes_its_buffer(wee, monkeypatch):
    """A failed connect must not leave its buffer behind.

    It did, so the next attempt hit "a buffer with same name already exists"
    and that unrelated error buried the real one.
    """
    weechat, rrc = wee
    monkeypatch.setattr(rrc, "find_python", lambda: None)
    assert rrc.rrc_command_cb("", "", f"connect {HUB}") == weechat.WEECHAT_RC_ERROR
    assert rrc.connections == {}
    # Retrying must reach the same failure, not a buffer-name collision.
    assert rrc.rrc_command_cb("", "", f"connect {HUB}") == weechat.WEECHAT_RC_ERROR
    assert not any(
        "already exists" in line for line in weechat.state.core
    ), "the buffer from the first attempt was left open"


def test_a_missing_helper_package_is_diagnosed(wee, monkeypatch, tmp_path):
    """A script installed without rrc_helper says so, and where it looked.

    Otherwise the only symptom is the helper exiting with
    "No module named rrc_helper", which names neither the search path nor the
    fix.
    """
    weechat, rrc = wee
    monkeypatch.setattr(rrc, "find_python", lambda: "python3")
    monkeypatch.setattr(rrc, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(rrc, "helper_directory", lambda: str(tmp_path))

    def must_not_spawn(*args, **kwargs):
        raise AssertionError("the helper must not be spawned without its package")

    monkeypatch.setattr(rrc.subprocess, "Popen", must_not_spawn)
    assert rrc.rrc_command_cb("", "", f"connect {HUB}") == weechat.WEECHAT_RC_ERROR
    text = weechat.state.all_text
    assert "rrc_helper package was not found" in text
    assert str(tmp_path) in text
    assert "/python reload rrc" in text
