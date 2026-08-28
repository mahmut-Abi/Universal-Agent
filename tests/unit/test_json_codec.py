from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from universal_agent.core import (
    JsonCodecError,
    dumps_json,
    loads_json,
    read_json_file,
    write_json,
    write_json_file,
)


def test_json_codec_dumps_sorted_compact_json() -> None:
    assert dumps_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_json_codec_dumps_pretty_json() -> None:
    assert dumps_json({"b": 2, "a": 1}, indent=True) == '{\n  "a": 1,\n  "b": 2\n}'


def test_json_codec_loads_json_from_text_and_bytes() -> None:
    assert loads_json('{"a":1}') == {"a": 1}
    assert loads_json(b'{"a":1}') == {"a": 1}


def test_json_codec_writes_stream_with_trailing_newline() -> None:
    buffer = StringIO()

    write_json(buffer, {"b": 2, "a": 1}, indent=True)

    assert buffer.getvalue() == '{\n  "a": 1,\n  "b": 2\n}\n'


def test_json_codec_reads_and_writes_files(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    write_json_file(path, {"b": 2, "a": 1})

    assert path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'
    assert read_json_file(path) == {"a": 1, "b": 2}
    assert tuple(tmp_path.glob(".*.tmp")) == ()


def test_json_codec_cleans_up_atomic_temp_file_on_write_failure(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    with pytest.raises(JsonCodecError, match="not JSON serializable"):
        write_json_file(path, object())

    assert not path.exists()
    assert tuple(tmp_path.glob(".*.tmp")) == ()


def test_json_codec_reports_invalid_json_and_non_serializable_values() -> None:
    with pytest.raises(JsonCodecError, match="invalid JSON"):
        loads_json("{")
    with pytest.raises(JsonCodecError, match="not JSON serializable"):
        dumps_json(object())
