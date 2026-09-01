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
"""Assert 100% API-documentation coverage in the pdoc standard.

Every module, public class, and public function under ``rrc.py`` and
``rrc_helper/`` must carry a docstring, per ``CLAUDE.md``. Implements the
``ACCEPTANCE.md`` B3 check so CI and a local reviewer run identical logic.
"""

import ast
import pathlib
import sys


def undocumented(path):
    """Yield ``file:line: name`` for every undocumented definition in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not ast.get_docstring(tree):
        yield f"{path}: missing module docstring"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # Leading underscore marks a private helper, which pdoc omits.
        if node.name.startswith("_"):
            continue
        if not ast.get_docstring(node):
            yield f"{path}:{node.lineno}: {node.name} missing docstring"


def main():
    """Exit non-zero if any public definition lacks a docstring."""
    targets = [pathlib.Path("rrc.py")] + sorted(
        pathlib.Path("rrc_helper").rglob("*.py")
    )
    findings = [f for p in targets if p.exists() for f in undocumented(p)]
    print("\n".join(findings) or "all documented")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
