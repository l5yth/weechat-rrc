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
"""Reticulum transport for an RRC hub connection.

Everything that touches RNS lives here, so :mod:`rrc.helper.session` can stay a
pure state machine. This module attaches to the operator's shared instance,
resolves a path to the hub, opens a Link, and reports what happens through
callbacks.

Reticulum configuration is out of scope for this project: a running shared
instance is assumed and never created, modified, or configured from here.

Callbacks fire on RNS's own threads. Callers must not touch session state from
them directly; the helper's main loop hands them to a queue first.
"""

from __future__ import annotations

import time
from typing import Callable

import RNS

from . import constants as C

#: How long to wait for a path to the hub before giving up.
DEFAULT_PATH_TIMEOUT = 30.0

#: How long to wait for the Link to finish establishing.
DEFAULT_LINK_TIMEOUT = 30.0

#: Reconnect backoff bounds, in seconds (``SPEC.md`` D5).
DEFAULT_BACKOFF_BASE = 10.0
DEFAULT_BACKOFF_CAP = 300.0


class LinkError(Exception):
    """Raised when a hub cannot be reached or a Link cannot be established."""


class Backoff:
    """Exponential reconnect delay, doubling to a ceiling.

    RRC sessions end when the Link ends and nothing is replayed afterwards, so
    reconnecting is cheap and frequent retries buy nothing. The delay doubles
    from :attr:`base` and stops at :attr:`cap`.
    """

    def __init__(
        self,
        base: float = DEFAULT_BACKOFF_BASE,
        cap: float = DEFAULT_BACKOFF_CAP,
    ) -> None:
        """Create a backoff starting at *base* seconds and capped at *cap*."""
        self.base = base
        self.cap = cap
        self.attempts = 0

    def next_delay(self) -> float:
        """Return the next delay in seconds and advance the sequence."""
        delay = min(self.cap, self.base * (2**self.attempts))
        self.attempts += 1
        return delay

    def reset(self) -> None:
        """Return to the initial delay after a successful connection."""
        self.attempts = 0


def parse_hub_hash(text: str) -> bytes:
    """Return the hub destination hash decoded from *text*.

    Args:
        text: 32 hex characters, optionally with surrounding whitespace and in
            any case.

    Returns:
        The 16-byte destination hash.

    Raises:
        LinkError: If *text* is not a well-formed destination hash. This is
            user input from ``/rrc connect``, so the message names the problem.
    """
    cleaned = text.strip().lower().removeprefix("0x")
    expected = C.IDENTITY_HASH_BYTES * 2
    if len(cleaned) != expected:
        raise LinkError(
            f"a hub address is {expected} hex characters, got {len(cleaned)}"
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise LinkError(f"hub address is not hexadecimal: {text!r}") from exc


def resolve_hub(
    dest_hash: bytes,
    timeout: float = DEFAULT_PATH_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> "RNS.Identity":
    """Find a path to *dest_hash* and recall the hub's identity.

    Args:
        dest_hash: The hub's 16-byte destination hash.
        timeout: Seconds to wait for a path before giving up.
        sleep: Sleep function, injectable for tests.
        clock: Monotonic time source, injectable for tests.

    Returns:
        The hub's Reticulum identity, needed to construct an outbound
        destination.

    Raises:
        LinkError: If no path appears within *timeout*, or if a path exists but
            the identity cannot be recalled. The first case is by far the most
            common and usually means the local node has no route to the hub.
    """
    if not RNS.Transport.has_path(dest_hash):
        started = clock()
        RNS.Transport.request_path(dest_hash)
        deadline = started + timeout
        while not RNS.Transport.has_path(dest_hash):
            if clock() >= deadline:
                # Report the time actually spent waiting, not the configured
                # timeout: the two differ, and quoting the setting back at the
                # user tells them nothing they did not already configure.
                raise LinkError(
                    f"no path to {dest_hash.hex()} after "
                    f"{clock() - started:.0f}s of path discovery; the local "
                    f"Reticulum node has no route to this hub. Check "
                    f"'rnstatus' shows your interfaces"
                )
            sleep(0.1)
    identity = RNS.Identity.recall(dest_hash)
    if identity is None:
        raise LinkError(f"could not recall the identity of {dest_hash.hex()}")
    return identity


def hub_destination(hub_identity: "RNS.Identity") -> "RNS.Destination":
    """Return the outbound ``rrc.hub`` destination for *hub_identity*.

    1-RRC §Transport names ``rrc.hub`` as the destination convention, which is
    an app name of ``rrc`` with a single ``hub`` aspect.
    """
    return RNS.Destination(
        hub_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        C.HUB_APP_NAME,
        *C.HUB_ASPECTS,
    )


class HubLink:
    """One Reticulum Link to an RRC hub.

    The Link *is* the session (2-RRC §Sessions and First Contact). When it
    closes, this object is spent; the caller builds a new one to reconnect.
    """

    def __init__(
        self,
        dest_hash: bytes,
        identity: "RNS.Identity",
        on_up: Callable[[], None],
        on_frame: Callable[[bytes], None],
        on_down: Callable[[str], None],
    ) -> None:
        """Prepare a Link to *dest_hash* authenticated as *identity*.

        Args:
            dest_hash: The hub's destination hash.
            identity: This client's identity, presented to the hub once the
                Link is up.
            on_up: Called when the Link is established and identified.
            on_frame: Called with each inbound packet payload.
            on_down: Called with a reason when the Link closes.
        """
        self.dest_hash = dest_hash
        self.identity = identity
        self.link: "RNS.Link | None" = None
        self._on_up = on_up
        self._on_frame = on_frame
        self._on_down = on_down

    def open(
        self,
        path_timeout: float = DEFAULT_PATH_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Resolve the hub and start establishing the Link.

        Raises:
            LinkError: If the hub cannot be resolved. Link establishment itself
                is asynchronous; success arrives via the ``on_up`` callback.
        """
        destination = hub_destination(
            resolve_hub(self.dest_hash, path_timeout, sleep, clock)
        )
        self.link = RNS.Link(destination)
        self.link.set_link_established_callback(self._established)
        self.link.set_packet_callback(self._packet)
        self.link.set_link_closed_callback(self._closed)

    def send(self, data: bytes) -> None:
        """Put one encoded envelope on the Link.

        Raises:
            LinkError: If the Link is not currently usable. Callers surface
                this to the user rather than dropping the message silently.
        """
        if self.link is None or self.link.status != RNS.Link.ACTIVE:
            raise LinkError("not connected to a hub")
        RNS.Packet(self.link, data).send()

    def close(self) -> None:
        """Tear the Link down, if one is open."""
        if self.link is not None:
            self.link.teardown()
            self.link = None

    # -- RNS callbacks, invoked on Reticulum's own threads -----------------

    def _established(self, link: "RNS.Link") -> None:
        """Present our identity to the hub, then report the Link is up.

        The hub authenticates the peer from the Link, so without ``identify``
        it would see an anonymous client and could not route direct messages
        or apply per-identity permissions.
        """
        link.identify(self.identity)
        self._on_up()

    def _packet(self, message: bytes, packet: object) -> None:
        """Forward an inbound packet payload to the session."""
        self._on_frame(message)

    def _closed(self, link: "RNS.Link") -> None:
        """Report why the Link closed, in words a user can act on."""
        reasons = {
            RNS.Link.TIMEOUT: "link timed out",
            RNS.Link.INITIATOR_CLOSED: "disconnected",
            RNS.Link.DESTINATION_CLOSED: "hub closed the link",
        }
        self._on_down(reasons.get(link.teardown_reason, "link closed"))
