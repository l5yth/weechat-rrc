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
"""Helper process for the WeeChat RRC plugin.

This package owns everything that touches Reticulum: the shared-instance
attachment, Link lifecycle, identity handling, and RRC wire encoding. It runs
in its own process so that a blocking or crashing RNS call cannot freeze the
WeeChat UI, and so that ``RNS.Reticulum()``'s process-wide singleton never
collides with ``/script reload`` (``SPEC.md`` D1).

The WeeChat-facing half of the plugin lives in ``rrc.py`` and imports nothing
outside the standard library.
"""

__version__ = "0.1.0"
