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
"""Finding a Python interpreter that can run the helper (``SPEC.md`` D10).

The helper needs ``RNS`` and ``cbor2``; the WeeChat process needs neither, and
must never import them (``SPEC.md`` D1). This module decides which interpreter
to spawn, and explains itself when no candidate qualifies, because the first
``/rrc connect`` is where a user discovers the dependency at all.
"""

from __future__ import annotations

import os
import subprocess

import weechat

from rrc.ui import SCRIPT_NAME

#: The directory holding the ``rrc`` package's own files, used to locate the
#: helper beside the script. Derived from this module's path rather than the
#: script's: WeeChat clears a *script*'s ``__file__`` before any callback runs,
#: which once sent the search to the WeeChat data directory and broke
#: ``/rrc connect`` for every install layout but one. An imported module keeps
#: its ``__file__``, so this cannot happen here.
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


#: Import check a candidate interpreter must pass to run the helper.
PROBE = "import RNS, cbor2"

#: Interpreters tried when ``helper.python`` is not configured, in order.
#: ``RRC_VENV`` is the location the setup instructions suggest, so a user who
#: follows them needs no configuration afterwards.
RRC_VENV = "~/.local/share/weechat/rrc-venv/bin/python"
FALLBACK_PYTHONS = ("python3", RRC_VENV, "~/.venv/bin/python")


# -- helper interpreter discovery -----------------------------------------


def probe_python(path: str) -> bool:
    """Return ``True`` if *path* is an interpreter that can run the helper.

    Both Reticulum and cbor2 must be importable; a Python with only one of them
    would fail later and less clearly.
    """
    try:
        return (
            subprocess.run(
                [os.path.expanduser(path), "-c", PROBE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def find_python() -> str | None:
    """Return the first interpreter able to run the helper, or ``None``.

    Resolution order is the ``helper.python`` option, then ``$RRC_PYTHON``,
    then the fallbacks. Nothing is hardcoded: on a machine where Reticulum is
    installed system-wide, the first fallback wins.
    """
    candidates = [
        weechat.config_get_plugin("helper.python"),
        os.environ.get("RRC_PYTHON", ""),
        *FALLBACK_PYTHONS,
    ]
    for candidate in candidates:
        if candidate and probe_python(candidate):
            return os.path.expanduser(candidate)
    return None


def missing_python_help() -> list[str]:
    """Return step-by-step guidance for installing the helper's dependencies.

    This fires the first time someone tries to connect, so it has to be enough
    on its own. Distribution packages come first: they need no virtualenv and
    no configuration afterwards, because they land in the same interpreter
    WeeChat already embeds.
    """
    tried = ", ".join(os.path.expanduser(p) for p in FALLBACK_PYTHONS)
    venv = os.path.expanduser(os.path.dirname(os.path.dirname(RRC_VENV)))
    return [
        "no Python with Reticulum (RNS) and cbor2 was found.",
        f"tried: {tried}",
        "",
        "Option 1 - install them for your system Python. Nothing else to do:",
        "  Arch     pikaur -S python-rns python-cbor2",
        "  Debian   sudo apt install python3-rns python3-cbor2",
        "  other    pip install --user rns cbor2",
        "",
        "Option 2 - if your distribution refuses to install them system-wide",
        "(PEP 668, 'externally-managed-environment'), use a virtualenv. This",
        "path is searched automatically, so no further setup is needed:",
        f"  python3 -m venv {venv}",
        f"  {venv}/bin/pip install rns cbor2",
        "",
        "Option 3 - point the plugin at a Python you already have:",
        f"  /set plugins.var.python.{SCRIPT_NAME}.helper.python /path/to/python",
    ]


def missing_helper_help(directory: str) -> list[str]:
    """Return guidance for a script installed without its helper package.

    Checked before spawning, because otherwise the only symptom is the helper
    exiting with ``No module named rrc.helper``, which says nothing about where
    the script looked or what to copy.
    """
    searched = [SCRIPT_DIR, os.path.dirname(SCRIPT_DIR)] if SCRIPT_DIR else []
    searched.append(os.path.join(weechat.info_get("weechat_dir", ""), "python"))
    lines = [
        "the rrc.helper package was not found, so the helper cannot start.",
        "The rrc package must sit in python/, holding both rrc.py and helper.",
        "",
        "searched:",
    ]
    lines += [f"  {d}" for d in dict.fromkeys(d for d in searched if d)]
    lines += [
        "",
        f"this script was loaded from: {SCRIPT_DIR or '(unknown)'}",
        "",
        "Copy the package next to the script, for example:",
        f"  cp -r /path/to/weechat-rrc/rrc {directory}/",
        "then reload with: /python reload rrc",
    ]
    return lines


def helper_directory() -> str:
    """Return the directory containing the ``rrc`` package.

    The helper normally sits beside this script. The parent directory is also
    tried, so that copying the script into ``python/autoload/`` while leaving
    the package in ``python/`` still works. The WeeChat data directory is the
    last resort, for the case where the script has no file path at all.
    """
    candidates = []
    if SCRIPT_DIR:
        candidates += [SCRIPT_DIR, os.path.dirname(SCRIPT_DIR)]
    candidates.append(os.path.join(weechat.info_get("weechat_dir", ""), "python"))
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(candidate, "rrc", "helper")):
            return candidate
    return candidates[0]
