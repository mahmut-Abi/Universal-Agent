"""CLI ecosystem dispatch: thin re-export of the kernel implementation."""

from __future__ import annotations

from universal_agent.ecosystem.dispatch import (
    _dispatch_ecosystem,
    _dispatch_ecosystem_store,
)

__all__ = ["_dispatch_ecosystem", "_dispatch_ecosystem_store"]
