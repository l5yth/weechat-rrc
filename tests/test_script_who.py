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
"""Automatic ``/who`` after a confirmed join (ACCEPTANCE W1-W4, SPEC D15-D18).

``JOINED`` carries identity hashes and nothing else, so anyone already in a room
when this client arrives renders as a short hash. Asking the hub who is present
is the only way to put names on them, and it is the one hub command the plugin
sends on its own. These tests pin what it sends, when, and what it must leave
alone.
"""

from __future__ import annotations

ALICE = "1f5a80f61a6194267cf6b6df6a954adb"
BOB = "aabbccddeeff00112233445566778899"


def deliver(rrc, connection, process, *events):
    """Push *events* through the helper pipe and let the script read them."""
    process.emit(*events)
    rrc.rrc_stdout_cb(connection.name, "0")


def frames_from(process, mark):
    """Return the command frames the script wrote after index *mark*."""
    return process.written[mark:]


def join(rrc, connection, process, room, members=()):
    """Deliver our own ``joined`` for *room* and return what it sent."""
    mark = len(process.written)
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": room, "members": list(members)},
    )
    return frames_from(process, mark)


# -- W1: a confirmed join asks the hub who is present ---------------------


def test_on_join_asks_the_hub_who_is_present(connected):
    """Our own JOINED sends exactly one /who naming that room."""
    weechat, rrc, connection, process = connected
    assert join(rrc, connection, process, "#general", [ALICE]) == [
        {"op": "say", "room": "#general", "text": "/who #general"}
    ]


def test_on_join_sends_one_leading_slash_not_two(connected):
    """``//`` is WeeChat's input escape; the hub must see a single slash.

    Sending ``//who`` would reach the hub verbatim and not be recognised as a
    hub command at all.
    """
    weechat, rrc, connection, process = connected
    text = join(rrc, connection, process, "#general")[0]["text"]
    assert text.startswith("/who")
    assert not text.startswith("//")


def test_another_arrival_sends_no_on_join_who(connected):
    """Somebody else joining is not our join, so nothing is asked.

    Without this a busy room would emit one /who per arrival.
    """
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    mark = len(process.written)
    deliver(
        rrc,
        connection,
        process,
        {"op": "join", "room": "#general", "members": [BOB], "nick": "bob"},
    )
    assert frames_from(process, mark) == []


# -- W2: the option ------------------------------------------------------


def test_the_option_defaults_to_on(wee):
    """A fresh load enables the automatic request."""
    weechat, rrc = wee
    rrc.main()
    assert rrc.DEFAULTS["who_on_join"] == "on"
    assert weechat.config_get_plugin("who_on_join") == "on"


def test_the_option_set_to_off_sends_nothing(connected):
    """``who_on_join off`` stops the automatic request entirely."""
    weechat, rrc, connection, process = connected
    weechat.config_set_plugin("who_on_join", "off")
    assert join(rrc, connection, process, "#general", [ALICE]) == []


def test_the_option_never_touches_a_typed_who(connected):
    """A hub command the user types works whatever the option says.

    The option governs the automatic send only; SPEC D3's ``//`` escape is the
    user's and is not conditional on anything.
    """
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    for setting in ("on", "off"):
        weechat.config_set_plugin("who_on_join", setting)
        mark = len(process.written)
        rrc.rrc_input_cb(
            f"{connection.name}/#general",
            connection.rooms["#general"],
            "//who #general",
        )
        assert frames_from(process, mark) == [
            {"op": "say", "room": "#general", "text": "/who #general"}
        ]


# -- W3: rejoins after an outage -----------------------------------------


def test_a_rejoin_after_reconnect_asks_again(connected):
    """The post-WELCOME re-JOIN asks too; that is when names are missing.

    A reconnect rebuilds the nicklist from JOINED, which is hashes only, so
    this is the case the feature most exists for (SPEC D5, amended).
    """
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    deliver(
        rrc,
        connection,
        process,
        {"op": "state", "state": "down", "reason": "link closed"},
    )
    assert join(rrc, connection, process, "#general", [ALICE, BOB]) == [
        {"op": "say", "room": "#general", "text": "/who #general"}
    ]


def test_reconnect_into_several_rooms_asks_once_per_room(connected):
    """Each rejoined room gets its own request, and no room gets two.

    The helper re-JOINs every room the user is in after WELCOME, so a
    multi-room session produces a burst of confirmations; each must ask about
    its own room and only once.
    """
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    join(rrc, connection, process, "#rrc", [BOB])
    deliver(
        rrc,
        connection,
        process,
        {"op": "state", "state": "down", "reason": "link closed"},
    )
    mark = len(process.written)
    deliver(
        rrc,
        connection,
        process,
        {"op": "joined", "room": "#general", "members": []},
        {"op": "joined", "room": "#rrc", "members": []},
    )
    assert frames_from(process, mark) == [
        {"op": "say", "room": "#general", "text": "/who #general"},
        {"op": "say", "room": "#rrc", "text": "/who #rrc"},
    ]


# -- W4: the request is not privileged -----------------------------------


def test_the_request_is_an_ordinary_say_command(connected):
    """It is byte-identical to what typing //who produces.

    Travelling as a plain ``say`` is what leaves the hub's body-size and
    rate limits applying to it exactly as to any other message (SPEC D13, D17).
    """
    weechat, rrc, connection, process = connected
    automatic = join(rrc, connection, process, "#general", [ALICE])
    mark = len(process.written)
    rrc.rrc_input_cb(
        f"{connection.name}/#general", connection.rooms["#general"], "//who #general"
    )
    assert automatic == frames_from(process, mark)


def test_an_ordinary_listing_reply_is_rendered_and_learned(connected):
    """The hub's answer behaves as it always has: shown, and names adopted."""
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    deliver(
        rrc,
        connection,
        process,
        {
            "op": "chat",
            "kind": "notice",
            "src": "0" * 32,
            "body": f"members in #general: alice ({ALICE[:12]})",
        },
    )
    assert "alice" in weechat.state.buffers[connection.rooms["#general"]].nicks
    assert any(
        "members in #general" in line
        for line in weechat.state.buffers[connection.buffer].lines
    )


def test_an_ordinary_unknown_command_notice_is_not_special_cased(connected):
    """A hub that does not know /who answers with an ordinary notice.

    It renders identically whether the request was automatic or typed, which
    is what "no special path" means in practice, and it does not stop the next
    join from asking (SPEC D17).
    """
    weechat, rrc, connection, process = connected
    notice = {
        "op": "chat",
        "kind": "notice",
        "src": "0" * 32,
        "body": "unknown command: who",
    }
    join(rrc, connection, process, "#general", [ALICE])
    deliver(rrc, connection, process, notice)
    after_automatic = weechat.state.buffers[connection.buffer].lines[-1]

    rrc.rrc_input_cb(
        f"{connection.name}/#general", connection.rooms["#general"], "//who #general"
    )
    deliver(rrc, connection, process, notice)
    after_typed = weechat.state.buffers[connection.buffer].lines[-1]

    assert after_automatic == after_typed == "--\tunknown command: who"
    assert join(rrc, connection, process, "#rrc", []) == [
        {"op": "say", "room": "#rrc", "text": "/who #rrc"}
    ]


def test_an_ordinary_error_does_not_disable_the_request(connected):
    """A hub that rejects /who is told about once per join, not silenced.

    RRC's ERROR names no message it answers, so correlating one with the
    request would be a guess; SPEC D17 chooses not to guess.
    """
    weechat, rrc, connection, process = connected
    join(rrc, connection, process, "#general", [ALICE])
    deliver(
        rrc, connection, process, {"op": "error", "message": "unknown command: who"}
    )
    assert any(
        "unknown command: who" in line
        for line in weechat.state.buffers[connection.buffer].lines
    )
    assert join(rrc, connection, process, "#rrc", []) == [
        {"op": "say", "room": "#rrc", "text": "/who #rrc"}
    ]
