# SPDX-FileCopyrightText: 2026 Afri Blanck (@l5yth)
# SPDX-License-Identifier: Apache-2.0
#
# Copyright © 2026 Afri Blanck (@l5yth)
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
"""CI's shared instance has one owner and one RPC key (``ACCEPTANCE.md`` A1c).

The e2e job starts ``rnsd`` on the runner's fresh ``~/.reticulum`` and waits
for it before running ``make e2e``. A Reticulum program started during that
wait is a second instance racing ``rnsd``'s first start. Both create
``storage/transport_identity``, and when the other one writes last, the stored
identity is no longer the one ``rnsd`` derived its RPC key from. ``rnsd`` then
refuses the RPC key of every later process, the hub included. The hub's
``is_blackholed()`` check on a client's identity fails, the Link never learns
who the client is, and the hub drops every ``HELLO``.

``rnsd`` opens its RPC listener on ``instance_control_port`` only after it has
stored its transport identity, in RNS 1.6.0 and 1.6.1 alike. When it opens the
shared-instance port differs: 1.6.0 opens it before storing the identity, 1.6.1
after. The step's own script is run here under ``bash -e``, as the runner runs
it, among stand-ins that keep 1.6.0's order, the one in which waiting for the
shared-instance port is not enough:

* an ``rnsd``, first on ``PATH``, that opens the shared-instance port 0.1
  seconds after it starts and its RPC port 1.5 seconds after that, longer
  than the step's one-second poll;
* a recording stub, first on ``PATH``, for every other program the installed
  ``rns`` package declares;
* an ``RNS`` package, first on ``PYTHONPATH``, that records being imported.

What is checked is what ran and when, not the order of lines in the file.
``HOME`` is a temporary directory, and the two ports are moved to free ones, in
the config and wherever the script names them, so the stand-ins cannot collide
with a real instance on CI's ports.

Not seen: Reticulum started some other way, such as through ``make``. Nor
whether the step's wait saw the RPC listener or only outlasted it: a wait that
times out, or simply sleeps, after the stand-in has opened its RPC port passes.
And the step must start ``rnsd`` by name, as it does, for the stand-in to
replace it.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import os
import pathlib
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The workflow whose e2e job implements ``ACCEPTANCE.md`` A1.
WORKFLOW = ROOT / ".github" / "workflows" / "python.yml"

#: The e2e step that brings the shared instance up.
STEP = "Start a Reticulum shared instance"

#: The e2e step that writes CI's Reticulum config. Its config is read here and
#: the step itself is never run: it writes ``~/.reticulum/config``, which on
#: any machine but a CI runner is the operator's.
CONFIG_STEP = "Configure Reticulum for a single host"

#: When the stand-in ``rnsd`` opens the shared-instance port, then its RPC
#: port, in seconds after it starts. The gap stands for the time RNS 1.6.0's
#: ``rnsd`` spends storing its identity between the two. It is longer than the
#: step's one-second poll, so a step that waits for the shared-instance port
#: instead is caught every time rather than by luck.
SHARED_PORT_AT = 0.1
RPC_PORT_AT = 1.6

#: Seconds the step may take before the test gives up on it.
STEP_TIMEOUT = 60

#: Seconds to let stand-ins finish starting when the step ended before the RPC
#: listener was up, so the records hold what the step started and not only
#: what had got going by the time it ended.
SETTLE = 0.5


def _indent(line: str) -> int:
    """Return the number of leading spaces on *line*."""
    return len(line) - len(line.lstrip(" "))


def run_block(name: str) -> str:
    """Return the ``run: |`` block of the step called *name*, de-indented.

    Parsed by indentation rather than with a YAML library, which is not a
    dependency of this project. The search for ``run: |`` stops at the next
    step, and the block ends at the first non-blank line indented no deeper
    than its ``run:`` key.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"),
        None,
    )
    assert start is not None, f"the workflow has no step named {name!r}"
    run = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and _indent(lines[i]) <= _indent(lines[start]):
            break
        if lines[i].strip() == "run: |":
            run = i
            break
    assert run is not None, f"step {name!r} has no 'run: |' block"
    block = []
    for line in lines[run + 1 :]:
        if line.strip() and _indent(line) <= _indent(lines[run]):
            break
        block.append(line)
    assert any(line.strip() for line in block), f"step {name!r} runs nothing"
    depth = min(_indent(line) for line in block if line.strip())
    return "\n".join(line[depth:] for line in block).strip("\n") + "\n"


def ci_config() -> str:
    """Return the Reticulum config CI writes, as the config step's heredoc has it."""
    match = re.search(r"<<'?(\w+)'?\n(.*?)\n\1\n", run_block(CONFIG_STEP), re.DOTALL)
    assert match, f"no heredoc found in {CONFIG_STEP!r}"
    return match.group(2) + "\n"


def config_port(config: str, key: str) -> int:
    """Return the port *config* sets for *key*."""
    match = re.search(rf"^\s*{key}\s*=\s*(\d+)\s*$", config, re.MULTILINE)
    assert match, f"CI's Reticulum config sets no {key}"
    return int(match.group(1))


def free_ports(count: int) -> list[int]:
    """Return *count* distinct TCP ports on 127.0.0.1 that nothing listens on."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def reticulum_programs() -> list[str]:
    """Return every console program the installed ``rns`` package declares."""
    try:
        dist = importlib.metadata.distribution("rns")
    except importlib.metadata.PackageNotFoundError:
        pytest.fail("rns is not installed, so its programs cannot be stood in for")
    return sorted(
        {ep.name for ep in dist.entry_points if ep.group == "console_scripts"}
    )


@dataclasses.dataclass
class StepRun:
    """What happened while the step's own script ran among the stand-ins."""

    #: One line per start, in no particular order: ``rnsd started`` for the
    #: owner, and ``<what> up`` or ``<what> down`` for anything else, by
    #: whether rnsd's RPC listener was already open at that moment.
    records: list[str]
    #: Whether rnsd's RPC listener was open when the script exited.
    listening_at_exit: bool
    #: The script's exit status under ``bash -e``.
    status: int
    #: The script's stdout and stderr, together.
    output: str


def run_step(tmp: pathlib.Path) -> StepRun:
    """Run the step's own script under ``bash -e`` among the stand-ins, in *tmp*."""
    config = ci_config()
    shared, rpc = free_ports(2)
    moves = {
        str(config_port(config, "shared_instance_port")): str(shared),
        str(config_port(config, "instance_control_port")): str(rpc),
    }
    named = re.compile(r"(?<!\d)(" + "|".join(moves) + r")(?!\d)")

    def move(text: str) -> str:
        """Return *text* with CI's two ports replaced by the free ones.

        One pass, so a free port that happens to equal one of CI's is never
        moved a second time.
        """
        return named.sub(lambda match: moves[match.group(1)], text)

    home, bin_dir, fake = tmp / "home", tmp / "bin", tmp / "fake" / "RNS"
    for directory in (home / ".reticulum", bin_dir, fake):
        directory.mkdir(parents=True)
    (home / ".reticulum" / "config").write_text(move(config), encoding="utf-8")
    script = tmp / "step.sh"
    script.write_text(move(run_block(STEP)), encoding="utf-8")
    log, listening = tmp / "starts.log", tmp / "listening"
    pids, stop = tmp / "rnsd.pids", tmp / "stop"

    # The owner's ports, opened in rnsd's order. Every wait watches for the
    # test's stop file, so the owner ends with the test wherever it has got to.
    owner = tmp / "owner.py"
    owner.write_text(
        "import pathlib, socket, sys, time\n"
        "def wait(seconds):\n"
        "    end = time.monotonic() + seconds\n"
        "    while time.monotonic() < end:\n"
        f"        if pathlib.Path({str(stop)!r}).exists():\n"
        "            sys.exit(0)\n"
        "        time.sleep(0.02)\n"
        f"wait({SHARED_PORT_AT})\n"
        f"shared = socket.create_server(('127.0.0.1', {shared}))\n"
        f"wait({RPC_PORT_AT - SHARED_PORT_AT})\n"
        f"rpc = socket.create_server(('127.0.0.1', {rpc}))\n"
        f"pathlib.Path({str(listening)!r}).touch()\n"
        f"wait({STEP_TIMEOUT})\n",
        encoding="utf-8",
    )
    # The rnsd stand-in records itself before anything else, so a step that
    # ends at once still shows it started rnsd, then becomes the owner: exec
    # keeps the PID it recorded.
    rnsd = (
        "#!/bin/sh\n"
        f"echo 'rnsd started' >> {shlex.quote(str(log))}\n"
        f"echo $$ >> {shlex.quote(str(pids))}\n"
        f"exec {shlex.quote(sys.executable)} -I {shlex.quote(str(owner))}\n"
    )

    def probe(name: str) -> str:
        """Return a stub that records *name* starting, then succeeds."""
        return (
            "#!/bin/sh\n"
            f"if [ -e {shlex.quote(str(listening))} ]; then at=up; else at=down; fi\n"
            f'echo "{name} $at" >> {shlex.quote(str(log))}\n'
        )

    for name in reticulum_programs():
        stub = bin_dir / name
        stub.write_text(rnsd if name == "rnsd" else probe(name), encoding="utf-8")
        stub.chmod(0o755)
    (fake / "__init__.py").write_text(
        "import os, sys\n"
        f"at = 'up' if os.path.exists({str(listening)!r}) else 'down'\n"
        f"with open({str(log)!r}, 'a') as out:\n"
        "    out.write(f'import RNS {at}\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    env = dict(
        os.environ,
        HOME=str(home),
        PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        PYTHONPATH=str(fake.parent),
    )
    output = tmp / "step.out"
    try:
        # Output goes to a file, not a pipe: the backgrounded owner inherits
        # it, and a pipe it holds open would keep this call waiting on it.
        with open(output, "w", encoding="utf-8") as out:
            status = subprocess.run(
                ["bash", "-e", str(script)],
                cwd=tmp,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=STEP_TIMEOUT,
                check=False,
            ).returncode
        listening_at_exit = listening.exists()
        if not listening_at_exit:
            time.sleep(SETTLE)
    finally:
        stop.touch()
        # Belt and braces: an owner that missed the stop file is ended here.
        for pid in pids.read_text().split() if pids.exists() else []:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
    return StepRun(
        records=log.read_text().splitlines() if log.exists() else [],
        listening_at_exit=listening_at_exit,
        status=status,
        output=output.read_text(encoding="utf-8"),
    )


def test_the_parser_reads_the_steps_own_script():
    """The step and CI's config are found, and neither runs into the next step.

    The behaviour test below runs what these return, so a parser that ran past
    a step, or found nothing, would make it pass or fail for the wrong reason.
    """
    assert "- name:" not in run_block(STEP)
    config = ci_config()
    assert "[reticulum]" in config and "- name:" not in config


def test_nothing_starts_reticulum_before_rnsd_rpc_listener_is_up(tmp_path):
    """The step starts rnsd and ends only once rnsd's RPC listener is open.

    Nothing else Reticulum, neither a program the ``rns`` package declares nor a
    Python import of ``RNS``, may start before then: each would be an instance
    racing rnsd for its transport identity. Ending before then fails as well,
    because the next step, ``make e2e``, starts the hub and the helpers.
    """
    run = run_step(tmp_path)
    assert run.status == 0, f"the step failed:\n{run.output}"
    early = [record for record in run.records if record.endswith(" down")]
    assert early == [], (
        "these started before rnsd's RPC listener was up, racing rnsd for its "
        f"transport identity: {early}"
    )
    started = "rnsd started" in run.records
    assert run.listening_at_exit, (
        "the step ended before rnsd's RPC listener was up, so the next step's "
        "hub and helpers would race rnsd"
        + ("" if started else "; it never started rnsd at all")
    )
