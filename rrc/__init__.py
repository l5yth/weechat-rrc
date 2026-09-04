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
"""The weechat-rrc plugin: a WeeChat script, its UI modules, and its helper.

**This file must stay free of imports, permanently.** It is imported by two
processes that share nothing else. The WeeChat process reaches ``rrc.ui``
through it, and the helper process -- a *different* interpreter, the one with
Reticulum available -- imports it on the way to ``python -m rrc.helper``.
Anything here that reached for ``weechat`` would crash the helper on startup,
and anything that reached for ``RNS`` would drag Reticulum into the WeeChat
process, breaking the boundary ``SPEC.md`` D1 exists to hold.

The two halves never import each other: ``rrc.ui`` is stdlib-only and owns
buffers and rendering, ``rrc.helper`` owns every Reticulum call, and they speak
newline-delimited JSON over a pipe (``SPEC.md`` D8).
"""
