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
"""The WeeChat half of the plugin: buffers, rendering, and commands.

Stdlib-only, and it stays that way: every Reticulum call lives in
``rrc.helper``, in its own process (``SPEC.md`` D1). Nothing here imports that
package, and ``ACCEPTANCE.md`` A2 checks both halves by name.

This module holds only the plugin's identity and its configuration defaults,
so that :mod:`rrc.ui.render`, :mod:`rrc.ui.connection` and the script itself
can share them without importing one another in a circle.
"""

SCRIPT_NAME = "rrc"
SCRIPT_AUTHOR = "Afri Blank (@l5yth)"
SCRIPT_VERSION = "0.1.1"
SCRIPT_LICENSE = "Apache-2.0"
SCRIPT_DESC = "Reticulum Relay Chat (RRC) client"

#: Configuration options and their defaults, created on first load.
DEFAULTS = {
    "helper.python": "",
    "identity.path": "",
    "reconnect": "on",
    "autojoin": "",
    "who_on_join": "on",
}

#: Marks a private-buffer callback payload, distinguishing it from a room.
DM_PREFIX = "@"
