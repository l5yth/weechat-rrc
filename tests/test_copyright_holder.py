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
"""The copyright holder's name, spelled as ``SPEC.md`` D6 spells it.

D6 names the holder Afri Blanck (@l5yth). The surname once shipped misspelled,
so both places the name reaches are pinned here: the license header of every
tracked text file, and the author the script registers with WeeChat.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The holder exactly as ``SPEC.md`` D6 writes it.
HOLDER = "Afri Blanck (@l5yth)"

# A header line that names a holder: the SPDX tag or the copyright notice,
# behind any comment marker (``#``, or none inside README's ``<!--`` block).
# Anchored at the line start, so prose that merely mentions a notice is not
# taken for one.
HEADER = re.compile(r"^\W*(?:SPDX-FileCopyrightText:|Copyright ©) \d{4} (.+)$")


def tracked_texts():
    """Yield ``(name, text)`` for every tracked text file.

    Files are chosen as ``ACCEPTANCE.md`` B4 chooses them: whatever
    ``git ls-files`` lists, less binaries. A NUL byte marks a binary, as it
    does for ``grep -I``; an image has no comment syntax to carry a header.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    for name in filter(None, listing.stdout.split("\0")):
        data = (ROOT / name).read_bytes()
        if b"\0" not in data:
            yield name, data.decode("utf-8")


def test_every_header_names_the_holder_as_d6_spells_it():
    """Each SPDX tag and copyright notice names the holder exactly as D6 does."""
    seen, wrong = 0, []
    for name, text in tracked_texts():
        for number, line in enumerate(text.splitlines(), start=1):
            match = HEADER.match(line)
            if not match:
                continue
            seen += 1
            if match.group(1) != HOLDER:
                wrong.append(f"{name}:{number}: {match.group(1)}")
    # A pattern that stopped matching headers would otherwise pass vacuously.
    assert seen, "no header line found in any tracked file"
    assert wrong == []


def test_script_registers_the_holder_as_its_author(wee):
    """WeeChat records the script's author as D6 spells the holder."""
    weechat, rrc = wee
    rrc.main()
    assert weechat.state.registered["author"] == HOLDER
