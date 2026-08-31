from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class APIVersion:
    major: int
    minor: int
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> APIVersion:
        parts = value.strip().split(".")
        if not 2 <= len(parts) <= 3:
            raise ValueError(f"invalid API version: {value!r}")
        try:
            numbers = [int(p) for p in parts]
        except ValueError as exc:
            raise ValueError(f"invalid API version: {value!r}") from exc
        if any(n < 0 for n in numbers):
            raise ValueError(f"invalid API version: {value!r}")
        if len(numbers) == 2:
            numbers.append(0)
        return cls(major=numbers[0], minor=numbers[1], patch=numbers[2])

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    compatible: bool
    level: str
    notes: str = ""


CURRENT_API_VERSION = APIVersion(major=1, minor=0, patch=0)


class IncompatibleApiVersion(ValueError):
    pass


def check_api_compatibility(
    requested: APIVersion,
    *,
    current: APIVersion = CURRENT_API_VERSION,
) -> CompatibilityResult:
    if requested.major != current.major:
        return CompatibilityResult(
            compatible=False,
            level="incompatible",
            notes=(f"requested major {requested.major} differs from current major {current.major}"),
        )
    if requested.minor > current.minor:
        return CompatibilityResult(
            compatible=False,
            level="incompatible",
            notes=(
                f"requested minor {requested.minor} is ahead of current "
                f"minor {current.minor}; unknown features"
            ),
        )
    if requested.minor < current.minor:
        return CompatibilityResult(
            compatible=True,
            level="warning",
            notes=(f"requested minor {requested.minor} is behind current minor {current.minor}"),
        )
    return CompatibilityResult(
        compatible=True,
        level="compatible",
        notes="",
    )
