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
"""Turning protocol values into buffer lines: sanitising, colour, and tags.

Every string the hub sends reaches a WeeChat buffer through this module, which
makes it the whole of the plugin's rendering trust boundary: :func:`clean`
strips control characters, :func:`tag_value` and :func:`mention_key` strip what
would let a hub forge a tag or a highlight pattern, and :func:`show` is the one
place a line is printed. The invariants are ``SPEC.md`` D19-D26 and the checks
are ``ACCEPTANCE.md`` C8, N1-N6 and H1-H5.
"""

from __future__ import annotations

import weechat

from rrc.ui import SCRIPT_NAME

#: One type tag per rendered line, from the vocabulary fixed by ``SPEC.md``
#: D26. WeeChat exposes tags to ``/filter``, to ``hook_print`` and to
#: ``hotlist_max_level_nicks``, so these names are a public interface: renaming
#: one silently breaks a user's filters.
TAG_STATUS = "rrc_status"
TAG_JOIN = "rrc_join"
TAG_PART = "rrc_part"
TAG_MSG = "rrc_msg"
TAG_ACTION = "rrc_action"
TAG_NOTICE = "rrc_notice"
TAG_DIRECT = "rrc_direct"

#: WeeChat's own notify tags, which decide whether a line puts its buffer into
#: the hotlist and at which level (``SPEC.md`` D23). A line carrying none of
#: them is scored ``low``, which is exactly what join, part and status lines
#: want, so their absence is the value rather than an omission.
NOTIFY_MESSAGE = "notify_message"
NOTIFY_PRIVATE = "notify_private"

#: WeeChat's own tag for a line that must not be able to highlight, whatever
#: it happens to contain.
NO_HIGHLIGHT = "no_highlight"

#: The conversational content of each kind of buffer, for the
#: ``highlight_tags_restrict`` property (``SPEC.md`` D25). WeeChat refuses to
#: highlight a line carrying none of these, which makes join lines, part lines
#: and every status line structurally incapable of mentioning the user rather
#: than individually excused. That matters because the plugin quotes the user's
#: own mention keys back at them routinely -- "your identity is <hash>" and
#: "you are now known as <nick>" each contain one verbatim.
RESTRICT_ROOM = "rrc_msg,rrc_action,rrc_notice"
RESTRICT_PRIVATE = "rrc_direct"
RESTRICT_HUB = "rrc_notice"

#: Tags for a line the user wrote themselves. The hub echoes every message back
#: to its author, so without these your own words would light your own buffer
#: and highlight you for typing your own name. Verified against WeeChat 4.10:
#: neither the buffer's ``nick`` local variable nor the ``self_msg`` tag
#: suppresses a self-highlight, and only ``notify_none`` with ``no_highlight``
#: does. ``self_msg`` is kept because scripts hook it, not because it works.
SELF_TAGS = ("notify_none", NO_HIGHLIGHT, "self_msg")


# -- display helpers -------------------------------------------------------


def log(message: str) -> None:
    """Print a script-level message on WeeChat's core buffer."""
    show("", f"{SCRIPT_NAME}: {message}", TAG_STATUS)


def clean(text: object, fallback: str = "") -> str:
    """Return *text* with control characters removed, for safe display.

    Everything the hub sends is attacker-controlled. The helper already strips
    control characters, but this is the last point before the text reaches a
    buffer, so it is enforced here too rather than assumed.
    """
    if not isinstance(text, str):
        return fallback
    return "".join(ch for ch in text if ch.isprintable() or ch == " ")


def tag_value(text: object) -> str:
    """Return *text* reduced to something safe to interpolate into a tag.

    WeeChat's tag list is comma-separated, so a value spliced into it can close
    one tag and open another. This is not theoretical: a tag list built from a
    nickname containing a comma was verified against WeeChat 4.10 to yield a
    real, effective ``notify_highlight``. Unsanitised, a hub could choose its
    own notify level, forge ``no_log`` to keep a line out of the user's log
    files, or forge ``no_filter`` to make one unhideable (``SPEC.md`` D26).

    Commas and whitespace are removed on top of :func:`clean`, so what remains
    cannot be read as a separator by WeeChat or as a second word by anything
    parsing the tag. Two names that differ only in spacing therefore collide
    here, which costs nothing: the ``nick_`` tag is a convenience, and the
    ``rrc_id_`` tag beside it is the authoritative one (``SPEC.md`` D21, D26).
    """
    return "".join(ch for ch in clean(text) if ch != "," and not ch.isspace())


def mention_key(text: object) -> str:
    """Return *text* reduced to something safe to seed as a highlight word.

    WeeChat's ``highlight_words`` is a comma-separated list whose entries may
    contain ``*`` as a wildcard, so a hub-confirmed nickname reaching it
    unsanitised could match every line in every room -- a hub answering
    ``/nick`` with ``*`` would decide when the user's terminal beeps. The
    nickname is advisory and hub-supplied (``SPEC.md`` D13), which makes this
    the same class of problem as a formatting byte inside a colour code: a hub
    string entering a construct the plugin chose (``SPEC.md`` D24).

    :func:`tag_value` already removes the separator and whitespace, so only the
    wildcard is left to strip.
    """
    return "".join(ch for ch in tag_value(text) if ch != "*")


def short(identity_hash: str) -> str:
    """Return an abbreviated identity hash, for use when no nickname exists."""
    return identity_hash[:8] if identity_hash else "?"


def speaker(event: dict) -> str:
    """Return the display name for a message's sender.

    A nickname is advisory, so the authoritative short identity hash is shown
    whenever no nickname was supplied.
    """
    return clean(event.get("nick")) or short(event.get("src", ""))


def coloured(identity: str, name: str) -> str:
    """Return *name* wrapped in the colour WeeChat assigns to *identity*.

    The colour is keyed on the identity hash, never on the displayed name, so
    it follows the person through a rename and an impostor who takes somebody's
    nickname does not take their colour with it (``SPEC.md`` D21). WeeChat
    computes it: the user's own ``weechat.color.chat_nick_colors`` palette and
    ``weechat.look.nick_color_hash`` algorithm apply here exactly as they do in
    the irc plugin, and nothing in this repository selects or hashes a colour
    (``SPEC.md`` D20).

    The trailing reset is load-bearing. Without it the speaker's colour runs on
    past the name into the message body, which is hub-supplied text that must
    never carry formatting the plugin did not choose (``SPEC.md`` D22).

    Args:
        identity: The sender's full identity hash, as hex.
        name: The already-sanitised display name to wrap.

    Returns:
        The name with a leading colour code and a trailing reset.
    """
    return weechat.info_get("nick_color", identity) + name + weechat.color("reset")


def line_tags(kind: str, *notify: str, src: str = "", name: str = "") -> str:
    """Return the comma-separated tag string for one buffer line.

    A line with a sender carries both ``nick_<name>`` and ``rrc_id_<hash>``,
    deliberately. ``nick_`` is the conventional tag every WeeChat script,
    ``/filter`` recipe and ``hotlist_max_level_nicks`` entry expects, and
    ``rrc_id_`` is the authoritative one: this is D21's hint-versus-proof split
    expressed in tags, so a user who mutes ``nick_bob`` mutes whoever currently
    calls themselves bob, and one who keys on ``rrc_id_`` mutes a person.

    Args:
        kind: The line's type tag, one of the ``TAG_*`` vocabulary.
        notify: WeeChat's own tags for this line, if any. Passing none is
            meaningful: WeeChat scores a line with no ``notify_`` tag as
            ``low``, which is the level activity lines want.
        src: The sender's full identity hash, if the line has a sender.
        name: The sender's display name, if the line has a sender.

    Returns:
        The tag string to hand to ``weechat.prnt_date_tags``.
    """
    tags = [kind, *notify]
    # Sanitised, then dropped if nothing survives: an empty "nick_" tag would
    # be a tag the plugin did not mean to emit.
    for prefix, value in (("nick_", name), ("rrc_id_", src)):
        safe = tag_value(value)
        if safe:
            tags.append(prefix + safe)
    return ",".join(tags)


def show(
    buffer: str, text: str, kind: str, *notify: str, src: str = "", name: str = ""
) -> None:
    """Print *text* to *buffer*, tagged so WeeChat can score it.

    Every line this plugin renders goes through here. WeeChat reads the tags to
    decide whether the buffer enters the hotlist and at which of its four
    levels, and therefore in which of the user's ``weechat.color.status_data_*``
    colours the status bar shows it (``SPEC.md`` D23). Nothing here picks a
    colour or a hotlist entry; it states what kind of line this is and leaves
    the rest to WeeChat.

    The date is ``0``, which WeeChat reads as "now".

    Args:
        buffer: The buffer to print to; empty prints to WeeChat's core buffer.
        text: The already-rendered line, prefix and tab included.
        kind: The line's type tag, one of the ``TAG_*`` vocabulary.
        notify: WeeChat's own tags for this line, if any.
        src: The sender's full identity hash, if the line has a sender.
        name: The sender's display name, if the line has a sender.
    """
    weechat.prnt_date_tags(
        buffer, 0, line_tags(kind, *notify, src=src, name=name), text
    )
