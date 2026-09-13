from __future__ import annotations


class JsonHttpModelError(RuntimeError):
    """Model HTTP/SDK failure.

    ``transient`` marks failures that a retry can plausibly resolve (timeouts,
    connection errors); permanent failures (auth, invalid payload, HTTP 4xx)
    must not set it so the runtime fails fast instead of burning retries.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient
