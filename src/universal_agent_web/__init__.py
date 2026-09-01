"""Universal Agent Web Console — static client assets.

This package holds only static frontend assets (HTML/JS/CSS) for the Web
Console. It is a pure HTTP API client: it fetches the agentd Runtime API
(JSON + SSE) and never imports the universal_agent kernel, so it can be
extracted to its own repository together with the other client packages.

agentd locates these assets at runtime via ``importlib.resources`` and serves
them for its ``/console`` routes; the package is optional — without it agentd
serves a minimal fallback page.
"""

from __future__ import annotations

__all__: list[str] = []
