from __future__ import annotations

from universal_agent.runtime.api_version import (
    CURRENT_API_VERSION,
    APIVersion,
    CompatibilityResult,
    IncompatibleApiVersion,
    check_api_compatibility,
)


def test_parse_full() -> None:
    assert APIVersion.parse("1.2.3") == APIVersion(major=1, minor=2, patch=3)


def test_parse_minor_only() -> None:
    assert APIVersion.parse("1.2") == APIVersion(major=1, minor=2, patch=0)


def test_parse_invalid_format() -> None:
    for bad in ("", "1", "1.2.3.4", "a.b", "1.x.0", "1.-2.0"):
        try:
            APIVersion.parse(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_str_roundtrip() -> None:
    v = APIVersion(major=2, minor=5, patch=7)
    assert str(v) == "2.5.7"
    assert APIVersion.parse(str(v)) == v


def test_current_api_version() -> None:
    assert CURRENT_API_VERSION == APIVersion(major=1, minor=0, patch=0)
    assert str(CURRENT_API_VERSION) == "1.0.0"


def test_major_mismatch_incompatible() -> None:
    result = check_api_compatibility(
        APIVersion.parse("2.0.0"),
        current=CURRENT_API_VERSION,
    )
    assert isinstance(result, CompatibilityResult)
    assert result.compatible is False
    assert result.level == "incompatible"


def test_minor_ahead_incompatible() -> None:
    result = check_api_compatibility(
        APIVersion.parse("1.1.0"),
        current=CURRENT_API_VERSION,
    )
    assert result.compatible is False
    assert result.level == "incompatible"


def test_minor_behind_warning() -> None:
    result = check_api_compatibility(
        APIVersion.parse("1.0.0"),
        current=APIVersion(major=1, minor=3, patch=0),
    )
    assert result.compatible is True
    assert result.level == "warning"


def test_patch_difference_compatible() -> None:
    result = check_api_compatibility(
        APIVersion.parse("1.0.5"),
        current=APIVersion(major=1, minor=0, patch=9),
    )
    assert result.compatible is True
    assert result.level == "compatible"


def test_exact_match_compatible() -> None:
    result = check_api_compatibility(
        APIVersion.parse("1.0.0"),
        current=CURRENT_API_VERSION,
    )
    assert result.compatible is True
    assert result.level == "compatible"


def test_default_current() -> None:
    result = check_api_compatibility(APIVersion.parse("1.0.0"))
    assert result == CompatibilityResult(compatible=True, level="compatible", notes="")


def test_incompatible_api_version_is_value_error() -> None:
    assert issubclass(IncompatibleApiVersion, ValueError)
