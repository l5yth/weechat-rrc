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
