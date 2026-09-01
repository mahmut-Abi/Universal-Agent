"""Value types used by the Universal Agent client SDK.

The SDK deliberately avoids importing the universal_agent kernel: client
packages (CLI/TUI/Web) depend only on this SDK so they can be extracted to
their own repository without dragging the runtime along.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NewType

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonMapping = Mapping[str, JsonValue]

SessionId = NewType("SessionId", str)

__all__ = ["JsonMapping", "JsonValue", "SessionId"]
