from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from io import StringIO
from pathlib import Path

import pytest

from universal_agent.core import (
    JsonCodecError,
    dumps_json,
    loads_json,
    read_json_file,
    to_json_object,
    to_json_value,
    write_json,
    write_json_file,
)


class _Status(StrEnum):
    RUNNING = "running"


class _UnknownObject:
    def __str__(self) -> str:
        return "unknown-object"


@dataclass(frozen=True, slots=True)
class _Event:
    status: _Status
    observed_at: datetime
    labels: tuple[str, ...]


class _ReadOnlyMapping(Mapping[object, object]):
    def __init__(self, values: Mapping[object, object]) -> None:
        self._values = values

    def __getitem__(self, key: object) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@pytest.mark.contract
def test_json_codec_dumps_sorted_compact_json() -> None:
    assert dumps_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


@pytest.mark.contract
def test_json_codec_dumps_pretty_json() -> None:
    assert dumps_json({"b": 2, "a": 1}, indent=True) == '{\n  "a": 1,\n  "b": 2\n}'


@pytest.mark.contract
def test_json_codec_loads_json_from_text_and_bytes() -> None:
    assert loads_json('{"a":1}') == {"a": 1}
    assert loads_json(b'{"a":1}') == {"a": 1}


@pytest.mark.contract
def test_json_codec_coerces_objects_to_json_values() -> None:
    payload = to_json_value(
        {
            1: _Status.RUNNING,
            "observed_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            "items": (_Status.RUNNING,),
        },
        fallback_to_string=True,
    )

    assert payload == {
        "1": "running",
        "items": ["running"],
        "observed_at": "2026-01-02T03:04:05+00:00",
    }


@pytest.mark.contract
def test_json_codec_uses_orjson_defaults_for_mapping_and_sequence_compatibility() -> None:
    payload = to_json_value(_ReadOnlyMapping({1: "one", "items": range(2)}))

    assert payload == {"1": "one", "items": [0, 1]}
    assert dumps_json(_ReadOnlyMapping({"b": 2, "a": 1})) == '{"a":1,"b":2}'


@pytest.mark.contract
def test_json_codec_can_stringify_unknown_objects_for_projection_boundaries() -> None:
    assert to_json_value({"value": _UnknownObject()}, fallback_to_string=True) == {
        "value": "unknown-object"
    }


@pytest.mark.contract
def test_json_codec_projects_dataclasses_to_json_objects() -> None:
    payload = to_json_object(
        _Event(
            status=_Status.RUNNING,
            observed_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            labels=("api", "prod"),
        )
    )

    assert payload == {
        "labels": ["api", "prod"],
        "observed_at": "2026-01-02T03:04:05+00:00",
        "status": "running",
    }


@pytest.mark.contract
def test_json_codec_rejects_non_object_projection() -> None:
    with pytest.raises(JsonCodecError, match="did not serialize to a JSON object"):
        to_json_object(["not", "an", "object"])


@pytest.mark.contract
def test_json_codec_writes_stream_with_trailing_newline() -> None:
    buffer = StringIO()

    write_json(buffer, {"b": 2, "a": 1}, indent=True)

    assert buffer.getvalue() == '{\n  "a": 1,\n  "b": 2\n}\n'


@pytest.mark.contract
def test_json_codec_reads_and_writes_files(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    write_json_file(path, {"b": 2, "a": 1})

    assert path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'
    assert read_json_file(path) == {"a": 1, "b": 2}
    assert tuple(tmp_path.glob(".*.tmp")) == ()


@pytest.mark.contract
def test_json_codec_cleans_up_atomic_temp_file_on_write_failure(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    with pytest.raises(JsonCodecError, match="not JSON serializable"):
        write_json_file(path, object())

    assert not path.exists()
    assert tuple(tmp_path.glob(".*.tmp")) == ()


@pytest.mark.contract
def test_json_codec_reports_invalid_json_and_non_serializable_values() -> None:
    with pytest.raises(JsonCodecError, match="invalid JSON"):
        loads_json("{")
    with pytest.raises(JsonCodecError, match="not JSON serializable"):
        dumps_json(object())
