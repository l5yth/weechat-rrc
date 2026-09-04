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
"""A stand-in for WeeChat's ``weechat`` module.

WeeChat supplies this module only inside its embedded interpreter, so the
script cannot be imported — let alone driven — without it. This fake records
every call, which lets the tests assert what the script displayed, which
buffers it opened, and which hooks it installed, all without a running WeeChat.

The real API is stringly typed: buffers and hooks are opaque pointer strings and
callbacks are passed by *name*, to be resolved against the script's globals.
Both quirks are reproduced here, because both shape how the script is written.
"""

from __future__ import annotations

WEECHAT_RC_OK = 0
WEECHAT_RC_OK_EAT = 1
WEECHAT_RC_ERROR = -1

WEECHAT_HOOK_SIGNAL_STRING = "string"


class Buffer:
    """A recorded buffer: its properties, its lines, and its nicklist."""

    def __init__(self, name, input_cb, input_data, close_cb, close_data):
        """Record how the buffer was created."""
        self.name = name
        self.input_cb = input_cb
        self.input_data = input_data
        self.close_cb = close_cb
        self.close_data = close_data
        self.properties = {}
        self.sets = []
        self.highlight_words = []
        self.lines = []
        self.tags = []
        self.nicks = {}
        self.groups = {}
        self.closed = False

    @property
    def text(self):
        """Return every line printed to this buffer, prefixes stripped."""
        return [line.split("\t", 1)[-1] for line in self.lines]

    def tags_for(self, needle):
        """Return the tags of the last printed line containing *needle*.

        Tags are what WeeChat scores a line by, so a test that cannot see them
        cannot check the hotlist level at all (``SPEC.md`` D23). Returned split
        on commas, which is how WeeChat itself parses the string, so that a
        forged separator shows up as two tags rather than hiding inside one.
        """
        for line, tags in reversed(list(zip(self.lines, self.tags))):
            if needle in line:
                return tags.split(",") if tags else []
        raise LookupError(f"no line containing {needle!r}")


class State:
    """Everything the fake recorded, reset between tests."""

    def __init__(self):
        """Start with no buffers, hooks, config, or registration."""
        self.registered = None
        self.buffers = {}
        self.closed = {}
        self.core = []
        self.hooks = {}
        self.config = {}
        self.unhooked = []
        self.counter = 0
        self.nick_colors = {}
        self.core_tags = []

    def reset(self):
        """Forget everything, as if WeeChat had just started."""
        self.__init__()

    def buffer(self, name):
        """Return the open buffer whose short name is *name*, or ``None``."""
        for buf in self.buffers.values():
            if buf.name == name:
                return buf
        return None

    def any_buffer(self, name):
        """Return the buffer called *name*, open or closed, or ``None``.

        Closed buffers are kept so a test can still inspect what was printed to
        one before it was closed.
        """
        for buf in list(self.buffers.values()) + list(self.closed.values()):
            if buf.name == name:
                return buf
        return None

    @property
    def all_text(self):
        """Return every line printed to any buffer, open or closed."""
        lines = []
        for buf in list(self.buffers.values()) + list(self.closed.values()):
            lines += buf.text
        return "\n".join(lines)

    def pointer(self, prefix):
        """Return a fresh opaque pointer string, as WeeChat would."""
        self.counter += 1
        return f"0x{prefix}{self.counter:04x}"


state = State()


def register(name, author, version, license_, description, shutdown, charset):
    """Record the script registration and report success."""
    state.registered = {
        "name": name,
        "author": author,
        "version": version,
        "license": license_,
        "description": description,
        "shutdown": shutdown,
        "charset": charset,
    }
    return True


def prnt(buffer, message):
    """Append *message* to *buffer*, or to the core buffer when empty."""
    prnt_date_tags(buffer, 0, "", message)


def prnt_date_tags(buffer, date, tags, message):
    """Append *message* to *buffer* along with the tags WeeChat would score it by.

    The tag string is recorded rather than discarded. Real WeeChat decides from
    these whether the buffer enters the hotlist and at which level, so a fake
    that dropped them would let every check in the hotlist suite pass while
    proving nothing — the same trap the colour fake fell into by returning "".
    """
    if not buffer:
        state.core.append(message)
        state.core_tags.append(tags)
        return
    state.buffers[buffer].lines.append(message)
    state.buffers[buffer].tags.append(tags)


def buffer_new(name, input_cb, input_data, close_cb, close_data):
    """Create a buffer and return its pointer, or "" if the name is taken.

    WeeChat refuses a duplicate name, prints "a buffer with same name already
    exists" on the core buffer, and returns an empty pointer. Anything later
    printed to that empty pointer lands on the core buffer instead.
    """
    if buffer_search("python", name):
        state.core.append(f"A buffer with same name ({name}) already exists")
        return ""
    pointer = state.pointer("b")
    state.buffers[pointer] = Buffer(name, input_cb, input_data, close_cb, close_data)
    return pointer


def buffer_set(buffer, prop, value):
    """Record a buffer property, modelling the highlight-word list properly.

    ``highlight_words_add`` and ``highlight_words_del`` are not properties, they
    are operations on one: a fake that stored them as if they were would let a
    wholesale assignment of ``highlight_words`` pass for an add, which is the
    distinction ``SPEC.md`` D24 turns on. Behaviour matched to WeeChat 4.10,
    probed directly: adding dedupes, deleting an absent word is a no-op, and
    adding an empty string does nothing.
    """
    buf = state.buffers[buffer]
    buf.sets.append((prop, value))
    if prop in ("highlight_words_add", "highlight_words_del"):
        for word in value.split(","):
            if not word:
                continue
            if prop.endswith("_add"):
                if word not in buf.highlight_words:
                    buf.highlight_words.append(word)
            elif word in buf.highlight_words:
                buf.highlight_words.remove(word)
        return
    if prop == "highlight_words":
        buf.highlight_words = [word for word in value.split(",") if word]
    buf.properties[prop] = value


def buffer_get_string(buffer, prop):
    """Return a recorded buffer property, or its name."""
    buf = state.buffers.get(buffer)
    if buf is None:
        return ""
    if prop in ("name", "short_name"):
        return buf.properties.get(prop, buf.name)
    if prop == "highlight_words":
        return ",".join(buf.highlight_words)
    return buf.properties.get(prop, "")


def buffer_search(plugin, name):
    """Return the pointer of the buffer called *name*, or an empty string."""
    for pointer, buf in state.buffers.items():
        if buf.name == name:
            return pointer
    return ""


def buffer_close(buffer):
    """Mark a buffer closed and move it out of the open set.

    The object is retained so tests can inspect what was printed to it.
    """
    buf = state.buffers.pop(buffer, None)
    if buf is not None:
        buf.closed = True
        state.closed[buffer] = buf


def current_buffer():
    """Return the most recently created buffer, or an empty string."""
    return next(reversed(state.buffers), "")


def hook_command(command, description, args, args_desc, completion, cb, data):
    """Record a command hook and return its pointer."""
    return _hook("command", command=command, callback=cb, data=data)


def hook_command_run(command, cb, data):
    """Record a command-run hook and return its pointer."""
    return _hook("command_run", command=command, callback=cb, data=data)


def hook_fd(fd, read, write, exception, cb, data):
    """Record a file-descriptor hook and return its pointer."""
    return _hook("fd", fd=fd, callback=cb, data=data)


def hook_timer(interval, align, max_calls, cb, data):
    """Record a timer hook and return its pointer."""
    return _hook("timer", interval=interval, callback=cb, data=data)


def _hook(kind, **fields):
    """Record one hook of *kind* and return its pointer."""
    pointer = state.pointer("h")
    state.hooks[pointer] = {"kind": kind, **fields}
    return pointer


def unhook(hook):
    """Record that a hook was removed."""
    state.unhooked.append(hook)
    state.hooks.pop(hook, None)


def config_is_set_plugin(option):
    """Return 1 if *option* has been set, else 0."""
    return 1 if option in state.config else 0


def config_set_plugin(option, value):
    """Set a plugin configuration option."""
    state.config[option] = value
    return 1


def config_get_plugin(option):
    """Return a plugin configuration option, or an empty string."""
    return state.config.get(option, "")


#: Colour names handed out by :func:`info_get`, in assignment order. Real
#: WeeChat picks from ``weechat.color.chat_nick_colors``; the exact names do not
#: matter here, only that they are distinct and stable.
NICK_COLORS = (
    "cyan",
    "magenta",
    "green",
    "brown",
    "lightblue",
    "lightcyan",
    "lightmagenta",
    "lightgreen",
    "31",
    "35",
    "38",
    "40",
    "49",
    "63",
    "70",
    "80",
)


def color(name):
    """Return a colour code shaped like the one real WeeChat returns.

    WeeChat answers ``color("reset")`` with ``0x1C`` and a colour name with a
    ``0x19``-prefixed code. An earlier version of this stand-in returned ``""``
    for everything, on the reasoning that colour codes are noise in tests. That
    was true until the script began emitting colour itself: against an empty
    fake, no code ever reaches a rendered line, so every colour assertion in the
    suite passes without testing anything and the C8 injection check passes for
    the wrong reason.
    """
    if name == "reset":
        return "\x1c"
    return "\x19" + name


def _nick_color_name(key):
    """Return a stable colour name for *key*, assigned on first sight.

    Deliberately **not** a model of WeeChat's djb2 hashing. It guarantees that
    distinct keys get distinct colours, which real WeeChat does not — its
    palette is finite and collisions are certain (``SPEC.md`` D21). Tests may
    therefore rely on "different identity, different colour" here, and must not
    assert anything about collisions, which are a property of the real
    implementation and not of this one.
    """
    if not key:
        return "default"  # what real WeeChat answers for an empty nick
    if key not in state.nick_colors:
        state.nick_colors[key] = NICK_COLORS[len(state.nick_colors) % len(NICK_COLORS)]
    return state.nick_colors[key]


def info_get(name, arguments):
    """Return a plausible value for the few info keys the script uses."""
    if name == "nick_color_name":
        return _nick_color_name(arguments)
    if name == "nick_color":
        return "\x19" + _nick_color_name(arguments)
    return {"weechat_dir": "/home/user/.config/weechat"}.get(name, "")


def nicklist_add_group(buffer, parent, name, group_color, visible):
    """Create a nicklist group and return its pointer."""
    pointer = state.pointer("g")
    state.buffers[buffer].groups[pointer] = name
    return pointer


def nicklist_add_nick(buffer, group, name, nick_color, prefix, prefix_color, visible):
    """Add a nick to a buffer's nicklist.

    The colour argument is recorded, not discarded: it is the whole of what
    nickname colouring changes about the nicklist, so a test that cannot see it
    cannot check it (``SPEC.md`` D19).
    """
    state.buffers[buffer].nicks[name] = {
        "group": group,
        "prefix": prefix,
        "color": nick_color,
    }
    return state.pointer("n")


def nicklist_remove_nick(buffer, nick):
    """Remove a nick, accepting either a pointer or a bare name."""
    state.buffers[buffer].nicks.pop(nick, None)


def nicklist_search_nick(buffer, group, name):
    """Return the nick's name if present, else an empty string."""
    return name if name in state.buffers[buffer].nicks else ""


def nicklist_remove_all(buffer):
    """Clear a buffer's nicklist."""
    state.buffers[buffer].nicks.clear()
