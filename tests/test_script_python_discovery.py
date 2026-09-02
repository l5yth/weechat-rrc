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
"""Helper interpreter discovery (ACCEPTANCE D1).

Nothing about the operator's Python layout may be hardcoded, and the failure
message must say what to do about it.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest


def test_probe_accepts_an_interpreter_with_both_packages(wee, monkeypatch):
    """A candidate qualifies only when RNS and cbor2 both import."""
    _, rrc = wee
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(rrc.subprocess, "run", run)
    assert rrc.probe_python("/usr/bin/python3") is True
    assert seen["cmd"][1:] == ["-c", "import RNS, cbor2"]


def test_probe_rejects_an_interpreter_missing_a_package(wee, monkeypatch):
    """A non-zero import check disqualifies the candidate."""
    _, rrc = wee
    monkeypatch.setattr(
        rrc.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1)
    )
    assert rrc.probe_python("/usr/bin/python3") is False


@pytest.mark.parametrize(
    "error", [OSError("no such file"), subprocess.TimeoutExpired("py", 15)]
)
def test_probe_survives_a_broken_candidate(wee, monkeypatch, error):
    """A missing or hanging interpreter is rejected, not raised."""
    _, rrc = wee

    def boom(cmd, **kwargs):
        raise error

    monkeypatch.setattr(rrc.subprocess, "run", boom)
    assert rrc.probe_python("/nonexistent/python") is False


def test_discovery_prefers_the_configured_interpreter(wee, monkeypatch):
    """The ``helper.python`` option wins over everything else."""
    weechat, rrc = wee
    weechat.config_set_plugin("helper.python", "/opt/rrc/python")
    monkeypatch.setenv("RRC_PYTHON", "/env/python")
    monkeypatch.setattr(rrc, "probe_python", lambda path: True)
    assert rrc.find_python() == "/opt/rrc/python"


def test_discovery_falls_back_to_the_environment(wee, monkeypatch):
    """``$RRC_PYTHON`` is used when no option is configured."""
    _, rrc = wee
    monkeypatch.setenv("RRC_PYTHON", "/env/python")
    monkeypatch.setattr(rrc, "probe_python", lambda path: True)
    assert rrc.find_python() == "/env/python"


def test_discovery_falls_back_to_the_system_interpreter(wee, monkeypatch):
    """A system Python with Reticulum installed needs no configuration."""
    _, rrc = wee
    monkeypatch.delenv("RRC_PYTHON", raising=False)
    monkeypatch.setattr(rrc, "probe_python", lambda path: path == "python3")
    assert rrc.find_python() == "python3"


def test_discovery_falls_back_to_a_virtualenv(wee, monkeypatch):
    """A venv is tried last, and the result is expanded to a real path."""
    _, rrc = wee
    monkeypatch.delenv("RRC_PYTHON", raising=False)
    monkeypatch.setattr(rrc, "probe_python", lambda path: "venv" in path)
    found = rrc.find_python()
    assert found.endswith("/.venv/bin/python")
    assert "~" not in found


def test_discovery_returns_none_when_nothing_qualifies(wee, monkeypatch):
    """With no usable interpreter, discovery reports failure rather than guess."""
    _, rrc = wee
    monkeypatch.delenv("RRC_PYTHON", raising=False)
    monkeypatch.setattr(rrc, "probe_python", lambda path: False)
    assert rrc.find_python() is None


def test_connect_without_an_interpreter_names_the_option(wee, monkeypatch):
    """The failure tells the user exactly which setting to change."""
    weechat, rrc = wee
    monkeypatch.setattr(rrc, "find_python", lambda: None)
    rrc.rrc_command_cb("", "", "connect 28c7c1a68c735693aa8e6b8193ed44b2")
    buffer = weechat.state.buffer("rrc.28c7c1a6")
    assert any("helper.python" in line for line in buffer.text)
    assert rrc.connections == {}  # the failed connection was not retained


def test_no_interpreter_path_is_hardcoded():
    """The shipped script must not name a machine-specific interpreter."""
    source = pathlib.Path("rrc.py").read_text(encoding="utf-8")
    assert "/home/" not in source
    assert "/usr/bin/python" not in source
    # The only venv reference is the tilde-prefixed fallback candidate.
    assert source.count(".venv") == 1
    assert "~/.venv/bin/python" in source


def test_helper_directory_sits_beside_the_script(wee):
    """The helper package is found next to the script file."""
    _, rrc = wee
    assert rrc.helper_directory().endswith("weechat-rrc")


def test_helper_directory_falls_back_to_the_weechat_directory(wee, monkeypatch):
    """Without ``__file__``, the WeeChat data directory is used."""
    _, rrc = wee
    monkeypatch.delitem(rrc.__dict__, "__file__")
    assert rrc.helper_directory() == "/home/user/.config/weechat/python"
