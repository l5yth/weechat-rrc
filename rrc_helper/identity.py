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
"""Reticulum identity storage for the RRC client.

In RRC the Reticulum identity *is* the account: 1-RRC §Identity makes the
identity hash the only authoritative identifier, and §Security Considerations
notes there is no recovery mechanism. Losing the file loses the identity
permanently, so this module is deliberately strict about file permissions.

``RNS.Identity.to_file`` writes mode ``0644``, which would leave a private key
readable by every account on the machine. Every write here is followed by a
``chmod`` to :data:`IDENTITY_MODE`, and every read refuses a file that is
group- or world-accessible.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import RNS

#: Directory holding client identities, alongside WeeChat's own configuration.
DEFAULT_DIR = Path("~/.config/weechat/rrc")

#: Permissions a private identity file must have: owner read/write only.
IDENTITY_MODE = 0o600

#: Permissions for the directory holding identities.
DIR_MODE = 0o700

#: Length of a Reticulum destination hash in hex characters.
HASH_HEX_LEN = 32


class IdentityError(Exception):
    """Raised when an identity cannot be created, loaded, or trusted."""


def default_dir() -> Path:
    """Return the directory identities are stored in, with ``~`` expanded."""
    return Path(os.path.expanduser(str(DEFAULT_DIR)))


def default_path() -> Path:
    """Return the path of the single shared identity used for every hub.

    This is the default from ``SPEC.md`` D4: one identity, so the user is
    recognisably the same person on every hub they join.
    """
    return default_dir() / "identity"


def per_hub_path(hub_hash: str) -> Path:
    """Return the identity path for *hub_hash* when per-hub identities are on.

    Args:
        hub_hash: The hub's destination hash as 32 hex characters.

    Returns:
        A path under ``ids/`` unique to that hub.

    Raises:
        IdentityError: If *hub_hash* is not a 32-character hex string. The
            value becomes a filename, so it is validated rather than trusted.
    """
    normalised = hub_hash.strip().lower()
    if len(normalised) != HASH_HEX_LEN:
        raise IdentityError(
            f"hub hash must be {HASH_HEX_LEN} hex characters, " f"got {len(normalised)}"
        )
    try:
        bytes.fromhex(normalised)
    except ValueError as exc:
        raise IdentityError(f"hub hash is not hexadecimal: {hub_hash!r}") from exc
    return default_dir() / "ids" / normalised


def is_private(path: Path) -> bool:
    """Return ``True`` if *path* is inaccessible to group and other users."""
    return not stat.S_IMODE(path.stat().st_mode) & 0o077


def load_or_create(path: Path) -> RNS.Identity:
    """Load the identity at *path*, creating one if the file does not exist.

    Args:
        path: Location of the identity file.

    Returns:
        The loaded or newly generated Reticulum identity.

    Raises:
        IdentityError: If an existing file is group- or world-accessible, or
            if it exists but cannot be parsed as an identity.
    """
    path = Path(path)
    if path.exists():
        return load(path)
    return create(path)


def create(path: Path) -> RNS.Identity:
    """Generate a new identity and write it to *path* with mode ``0600``.

    Args:
        path: Location to write the new identity file to.

    Returns:
        The newly generated identity.

    Raises:
        IdentityError: If the identity cannot be written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    identity = RNS.Identity()
    if not identity.to_file(str(path)):
        raise IdentityError(f"could not write identity to {path}")
    # RNS writes 0644; narrow it before the key is exposed for any longer
    # than the moment between these two calls.
    path.chmod(IDENTITY_MODE)
    return identity


def load(path: Path) -> RNS.Identity:
    """Load an existing identity from *path*, refusing an exposed key file.

    Args:
        path: Location of the identity file.

    Returns:
        The loaded identity.

    Raises:
        IdentityError: If the file is readable or writable by group or other
            users, or if it does not contain a valid identity.
    """
    path = Path(path)
    if not is_private(path):
        mode = stat.S_IMODE(path.stat().st_mode)
        raise IdentityError(
            f"identity {path} has mode {mode:04o}; a private key must not be "
            f"readable by other users. Fix it with: chmod 600 {path}"
        )
    identity = RNS.Identity.from_file(str(path))
    if identity is None:
        raise IdentityError(f"{path} does not contain a valid Reticulum identity")
    return identity
