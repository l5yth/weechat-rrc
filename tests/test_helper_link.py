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
"""Reticulum transport tests.

Reticulum is replaced with a fake so path resolution, Link establishment, and
teardown reporting can be driven deterministically and without a network.
"""

from __future__ import annotations

import pytest

from rrc.helper import link as link_mod
from rrc.helper.link import LinkError

HUB = bytes.fromhex("28c7c1a68c735693aa8e6b8193ed44b2")


class FakeLink:
    """Stand-in for ``RNS.Link`` recording callbacks and sent packets."""

    ACTIVE = "active"
    TIMEOUT = 1
    INITIATOR_CLOSED = 2
    DESTINATION_CLOSED = 3

    def __init__(self, destination):
        """Record the destination and start in the active state."""
        self.destination = destination
        self.status = FakeLink.ACTIVE
        self.teardown_reason = None
        self.identified_as = None
        self.torn_down = False
        self.on_established = None
        self.on_packet = None
        self.on_closed = None

    def set_link_established_callback(self, cb):
        """Register the established callback."""
        self.on_established = cb

    def set_packet_callback(self, cb):
        """Register the packet callback."""
        self.on_packet = cb

    def set_link_closed_callback(self, cb):
        """Register the closed callback."""
        self.on_closed = cb

    def identify(self, identity):
        """Record the identity presented to the hub."""
        self.identified_as = identity

    def teardown(self):
        """Record that the link was torn down."""
        self.torn_down = True


class FakePacket:
    """Stand-in for ``RNS.Packet`` recording what was sent."""

    sent = []

    def __init__(self, link, data):
        """Record the link and payload."""
        self.link = link
        self.data = data

    def send(self):
        """Record the send call."""
        FakePacket.sent.append(self.data)


@pytest.fixture
def fake_rns(monkeypatch):
    """Install a fake ``RNS`` into the link module and return it."""

    class FakeTransport:
        paths = set()
        requested = []

        @staticmethod
        def has_path(dest):
            return dest in FakeTransport.paths

        @staticmethod
        def request_path(dest):
            FakeTransport.requested.append(dest)

    class FakeIdentity:
        recalled = "hub-identity"

        @staticmethod
        def recall(dest):
            return FakeIdentity.recalled

    class FakeDestination:
        OUT = "out"
        SINGLE = "single"
        built = []

        def __init__(self, identity, direction, kind, app_name, *aspects):
            FakeDestination.built.append((identity, direction, kind, app_name, aspects))

    class RNS:
        Transport = FakeTransport
        Identity = FakeIdentity
        Destination = FakeDestination
        Link = FakeLink
        Packet = FakePacket

    FakeTransport.paths = set()
    FakeTransport.requested = []
    FakeIdentity.recalled = "hub-identity"
    FakeDestination.built = []
    FakePacket.sent = []
    monkeypatch.setattr(link_mod, "RNS", RNS)
    return RNS


# -- hub address parsing ---------------------------------------------------


def test_parse_hub_hash_accepts_a_plain_hash():
    """A bare 32-character hash is accepted."""
    assert link_mod.parse_hub_hash(HUB.hex()) == HUB


def test_parse_hub_hash_normalises_case_prefix_and_whitespace():
    """Users paste hashes in many shapes; all resolve to the same bytes."""
    assert link_mod.parse_hub_hash(f"  0x{HUB.hex().upper()}  ") == HUB


@pytest.mark.parametrize("bad", ["", "abc", "z" * 32, HUB.hex() + "00"])
def test_parse_hub_hash_rejects_malformed_input(bad):
    """A malformed hub address is reported rather than guessed at."""
    with pytest.raises(LinkError):
        link_mod.parse_hub_hash(bad)


# -- path resolution -------------------------------------------------------


def test_resolve_uses_an_existing_path(fake_rns):
    """When a path is already known, no path request is issued."""
    fake_rns.Transport.paths.add(HUB)
    assert link_mod.resolve_hub(HUB) == "hub-identity"
    assert fake_rns.Transport.requested == []


def test_resolve_requests_a_path_and_waits(fake_rns):
    """A missing path triggers a request and polling until it appears."""
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if len(polls) == 3:
            fake_rns.Transport.paths.add(HUB)

    assert link_mod.resolve_hub(HUB, sleep=sleep) == "hub-identity"
    assert fake_rns.Transport.requested == [HUB]
    assert len(polls) == 3


def test_resolve_times_out_with_an_actionable_message(fake_rns):
    """No route is the common failure, so the message says so."""
    clock = iter([0.0, 5.0, 40.0, 41.0])
    with pytest.raises(LinkError) as excinfo:
        link_mod.resolve_hub(
            HUB, timeout=30.0, sleep=lambda s: None, clock=lambda: next(clock)
        )
    assert "no path" in str(excinfo.value)
    assert "no route to this hub" in str(excinfo.value)
    assert "rnstatus" in str(excinfo.value)


def test_resolve_reports_time_actually_waited(fake_rns):
    """The message states measured elapsed time, not the configured timeout.

    Quoting the setting back is misleading: a user watching the buffer sees
    errors one backoff-plus-timeout apart and cannot reconcile that with a
    fixed number they never chose.
    """
    clock = iter([100.0, 105.0, 142.0, 147.0])
    with pytest.raises(LinkError) as excinfo:
        link_mod.resolve_hub(
            HUB, timeout=30.0, sleep=lambda s: None, clock=lambda: next(clock)
        )
    assert "after 47s" in str(excinfo.value)
    assert "after 30s" not in str(excinfo.value)


def test_resolve_reports_an_unrecallable_identity(fake_rns):
    """A path without a recallable identity cannot be linked to."""
    fake_rns.Transport.paths.add(HUB)
    fake_rns.Identity.recalled = None
    with pytest.raises(LinkError, match="could not recall"):
        link_mod.resolve_hub(HUB)


def test_hub_destination_uses_the_rrc_hub_aspects(fake_rns):
    """1-RRC §Transport: the destination is app ``rrc`` with aspect ``hub``."""
    link_mod.hub_destination("hub-identity")
    identity, direction, kind, app_name, aspects = fake_rns.Destination.built[0]
    assert identity == "hub-identity"
    assert (direction, kind) == ("out", "single")
    assert app_name == "rrc"
    assert aspects == ("hub",)


# -- link lifecycle --------------------------------------------------------


@pytest.fixture
def hub_link(fake_rns):
    """Return an opened :class:`HubLink` and the events it reports."""
    fake_rns.Transport.paths.add(HUB)
    events = []
    hub = link_mod.HubLink(
        HUB,
        identity="my-identity",
        on_up=lambda: events.append(("up", None)),
        on_frame=lambda data: events.append(("frame", data)),
        on_down=lambda reason: events.append(("down", reason)),
    )
    hub.open()
    return hub, events


def test_open_registers_every_callback(hub_link):
    """All three Link callbacks are wired before the Link can fire them."""
    hub, _ = hub_link
    assert hub.link.on_established is not None
    assert hub.link.on_packet is not None
    assert hub.link.on_closed is not None


def test_established_identifies_to_the_hub_then_reports_up(hub_link):
    """Without identify() the hub sees an anonymous peer and cannot route DMs."""
    hub, events = hub_link
    hub.link.on_established(hub.link)
    assert hub.link.identified_as == "my-identity"
    assert events == [("up", None)]


def test_inbound_packets_are_forwarded(hub_link):
    """Packet payloads reach the session unchanged."""
    hub, events = hub_link
    hub.link.on_packet(b"payload", object())
    assert events == [("frame", b"payload")]


@pytest.mark.parametrize(
    "reason,text",
    [
        (FakeLink.TIMEOUT, "link timed out"),
        (FakeLink.INITIATOR_CLOSED, "disconnected"),
        (FakeLink.DESTINATION_CLOSED, "hub closed the link"),
        (None, "link closed"),
    ],
)
def test_closed_reports_a_readable_reason(hub_link, reason, text):
    """Teardown reasons are translated into words a user can act on."""
    hub, events = hub_link
    hub.link.teardown_reason = reason
    hub.link.on_closed(hub.link)
    assert events == [("down", text)]


def test_send_puts_a_packet_on_the_link(hub_link):
    """An encoded envelope becomes one Reticulum packet."""
    hub, _ = hub_link
    hub.send(b"envelope")
    assert FakePacket.sent == [b"envelope"]


def test_send_without_a_link_is_refused(fake_rns):
    """Sending before connecting reports rather than dropping silently."""
    hub = link_mod.HubLink(HUB, None, lambda: None, lambda d: None, lambda r: None)
    with pytest.raises(LinkError, match="not connected"):
        hub.send(b"x")


def test_send_on_an_inactive_link_is_refused(hub_link):
    """A Link that is no longer active cannot carry messages."""
    hub, _ = hub_link
    hub.link.status = "closed"
    with pytest.raises(LinkError, match="not connected"):
        hub.send(b"x")


def test_close_tears_the_link_down(hub_link):
    """Closing tears down the Link and clears it."""
    hub, _ = hub_link
    link = hub.link
    hub.close()
    assert link.torn_down
    assert hub.link is None


def test_close_is_safe_without_a_link(fake_rns):
    """Closing an unopened connection is a no-op, not an error."""
    link_mod.HubLink(HUB, None, lambda: None, lambda d: None, lambda r: None).close()
