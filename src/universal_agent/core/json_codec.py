from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TextIO, cast

import orjson

from universal_agent.core.models import JsonValue


class JsonCodecError(ValueError):
    pass


def dumps_json(
    value: object,
    *,
    indent: bool = False,
    sort_keys: bool = True,
) -> str:
    option = 0
    if indent:
        option |= orjson.OPT_INDENT_2
    if sort_keys:
        option |= orjson.OPT_SORT_KEYS
    try:
        return orjson.dumps(_json_encodable(value), option=option).decode("utf-8")
    except TypeError as exc:
        raise JsonCodecError(f"value is not JSON serializable: {exc}") from exc


def to_json_value(value: object, *, fallback_to_string: bool = False) -> JsonValue:
    try:
        payload = orjson.dumps(_json_encodable(value, fallback_to_string=fallback_to_string))
    except TypeError as exc:
        raise JsonCodecError(f"value is not JSON serializable: {exc}") from exc
    return cast(JsonValue, loads_json(payload))


def loads_json(value: str | bytes | bytearray) -> object:
    try:
        return orjson.loads(value)
    except orjson.JSONDecodeError as exc:
        raise JsonCodecError(f"invalid JSON: {exc}") from exc


def write_json(
    out: TextIO,
    value: object,
    *,
    indent: bool = False,
    sort_keys: bool = True,
    trailing_newline: bool = True,
) -> None:
    out.write(dumps_json(value, indent=indent, sort_keys=sort_keys))
    if trailing_newline:
        out.write("\n")


def read_json_file(path: str | Path) -> object:
    return loads_json(Path(path).read_bytes())


def write_json_file(
    path: str | Path,
    value: object,
    *,
    indent: bool = True,
    sort_keys: bool = True,
    trailing_newline: bool = True,
) -> None:
    target = Path(path)
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            write_json(
                cast(TextIO, handle),
                value,
                indent=indent,
                sort_keys=sort_keys,
                trailing_newline=trailing_newline,
            )
        tmp_path.replace(target)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _json_encodable(value: object, *, fallback_to_string: bool = False) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if fallback_to_string and isinstance(value, Enum):
        return str(value.value)
    if fallback_to_string and isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_encodable(item, fallback_to_string=fallback_to_string)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_encodable(item, fallback_to_string=fallback_to_string) for item in value]
    if fallback_to_string:
        return str(value)
    return value
