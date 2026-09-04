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
"""Identity storage tests (ACCEPTANCE A5a).

An RRC identity is the account and cannot be recovered, so these tests focus on
the two ways that goes wrong: writing a key the rest of the machine can read,
and loading one that was already exposed.
"""

from __future__ import annotations

import os
import stat

import pytest

from rrc.helper import identity as I


def test_create_writes_a_private_key_file(tmp_path):
    """A new identity file is owner-only, despite RNS writing 0644 itself."""
    path = tmp_path / "identity"
    ident = I.create(path)
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(ident.hash) == 16


def test_create_makes_missing_parent_directories(tmp_path):
    """The identity directory is created on demand."""
    path = tmp_path / "nested" / "deeper" / "identity"
    I.create(path)
    assert path.exists()


def test_create_reports_a_write_failure(tmp_path, monkeypatch):
    """A failed write raises IdentityError rather than returning silently."""
    monkeypatch.setattr("RNS.Identity.to_file", lambda self, path: False, raising=True)
    with pytest.raises(I.IdentityError, match="could not write identity"):
        I.create(tmp_path / "identity")


def test_load_or_create_creates_then_reuses(tmp_path):
    """The same identity comes back on the second call; keys are not churned."""
    path = tmp_path / "identity"
    first = I.load_or_create(path)
    second = I.load_or_create(path)
    assert first.hash == second.hash


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o660])
def test_load_refuses_an_exposed_identity(tmp_path, mode):
    """Any group or other access on a private key is refused."""
    path = tmp_path / "identity"
    I.create(path)
    path.chmod(mode)
    with pytest.raises(I.IdentityError, match="must not be"):
        I.load(path)


def test_load_refusal_names_the_fix(tmp_path):
    """The refusal tells the user exactly how to repair the file."""
    path = tmp_path / "identity"
    I.create(path)
    path.chmod(0o644)
    with pytest.raises(I.IdentityError) as excinfo:
        I.load(path)
    assert f"chmod 600 {path}" in str(excinfo.value)


def test_load_rejects_a_file_that_is_not_an_identity(tmp_path):
    """A corrupt or unrelated file is reported, not returned as ``None``."""
    path = tmp_path / "identity"
    path.write_bytes(b"this is not an identity")
    path.chmod(0o600)
    with pytest.raises(I.IdentityError, match="valid Reticulum identity"):
        I.load(path)


def test_is_private_distinguishes_modes(tmp_path):
    """``is_private`` is true only when group and other have no access."""
    path = tmp_path / "identity"
    I.create(path)
    assert I.is_private(path)
    path.chmod(0o604)
    assert not I.is_private(path)


def test_default_path_sits_under_the_weechat_config(monkeypatch, tmp_path):
    """The default identity lives beside WeeChat's own configuration."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert I.default_path() == tmp_path / ".config/weechat/rrc/identity"
    assert I.default_dir() == tmp_path / ".config/weechat/rrc"


def test_per_hub_path_is_unique_per_hub(monkeypatch, tmp_path):
    """Per-hub identities are filed under the hub's destination hash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    hub = "28c7c1a68c735693aa8e6b8193ed44b2"
    assert I.per_hub_path(hub) == tmp_path / ".config/weechat/rrc/ids" / hub


def test_per_hub_path_normalises_case_and_whitespace(monkeypatch, tmp_path):
    """The same hub written differently resolves to one identity."""
    monkeypatch.setenv("HOME", str(tmp_path))
    hub = "28C7C1A68C735693AA8E6B8193ED44B2"
    assert I.per_hub_path(f"  {hub}  ").name == hub.lower()


@pytest.mark.parametrize("bad", ["", "abcd", "z" * 32, "../../etc/passwd"])
def test_per_hub_path_rejects_a_non_hash(bad):
    """The hash becomes a filename, so it is validated rather than trusted."""
    with pytest.raises(I.IdentityError):
        I.per_hub_path(bad)


def test_identity_hash_is_the_wire_width(tmp_path):
    """``identity.hash`` is the 16 bytes the envelope's K_SRC field needs."""
    from rrc.helper import constants as C

    assert len(I.create(tmp_path / "identity").hash) == C.IDENTITY_HASH_BYTES
