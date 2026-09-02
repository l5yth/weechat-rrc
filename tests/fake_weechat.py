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
        self.lines = []
        self.nicks = {}
        self.groups = {}
        self.closed = False

    @property
    def text(self):
        """Return every line printed to this buffer, prefixes stripped."""
        return [line.split("\t", 1)[-1] for line in self.lines]


class State:
    """Everything the fake recorded, reset between tests."""

    def __init__(self):
        """Start with no buffers, hooks, config, or registration."""
        self.registered = None
        self.buffers = {}
        self.core = []
        self.hooks = {}
        self.config = {}
        self.unhooked = []
        self.counter = 0

    def reset(self):
        """Forget everything, as if WeeChat had just started."""
        self.__init__()

    def buffer(self, name):
        """Return the buffer whose short name is *name*, or ``None``."""
        for buf in self.buffers.values():
            if buf.name == name:
                return buf
        return None

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
    if not buffer:
        state.core.append(message)
        return
    state.buffers[buffer].lines.append(message)


def buffer_new(name, input_cb, input_data, close_cb, close_data):
    """Create a buffer and return its pointer."""
    pointer = state.pointer("b")
    state.buffers[pointer] = Buffer(name, input_cb, input_data, close_cb, close_data)
    return pointer


def buffer_set(buffer, prop, value):
    """Record a buffer property."""
    state.buffers[buffer].properties[prop] = value


def buffer_get_string(buffer, prop):
    """Return a recorded buffer property, or its name."""
    buf = state.buffers.get(buffer)
    if buf is None:
        return ""
    if prop in ("name", "short_name"):
        return buf.properties.get(prop, buf.name)
    return buf.properties.get(prop, "")


def buffer_search(plugin, name):
    """Return the pointer of the buffer called *name*, or an empty string."""
    for pointer, buf in state.buffers.items():
        if buf.name == name:
            return pointer
    return ""


def buffer_close(buffer):
    """Mark a buffer closed and drop it."""
    buf = state.buffers.pop(buffer, None)
    if buf is not None:
        buf.closed = True


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


def color(name):
    """Return an empty string; colour codes only add noise in tests."""
    return ""


def info_get(name, arguments):
    """Return a plausible value for the few info keys the script uses."""
    return {"weechat_dir": "/home/user/.config/weechat"}.get(name, "")


def nicklist_add_group(buffer, parent, name, group_color, visible):
    """Create a nicklist group and return its pointer."""
    pointer = state.pointer("g")
    state.buffers[buffer].groups[pointer] = name
    return pointer


def nicklist_add_nick(buffer, group, name, nick_color, prefix, prefix_color, visible):
    """Add a nick to a buffer's nicklist."""
    state.buffers[buffer].nicks[name] = {"group": group, "prefix": prefix}
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
