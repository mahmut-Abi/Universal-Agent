from __future__ import annotations

from typing import cast

import pytest

from universal_agent.runtime.events import filter_events


def test_filter_events_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="event stream limit must be positive"):
        filter_events((), limit=0)
    with pytest.raises(ValueError, match="event stream limit must be an integer"):
        filter_events((), limit=cast(int, "1"))
