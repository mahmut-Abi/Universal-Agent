"""Compatibility entry point for the original P0 example."""

from __future__ import annotations

import asyncio

from p1_kubernetes_domain import main

if __name__ == "__main__":
    asyncio.run(main())
