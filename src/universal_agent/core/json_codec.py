from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import orjson


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
    with Path(path).open("w", encoding="utf-8") as handle:
        write_json(
            handle,
            value,
            indent=indent,
            sort_keys=sort_keys,
            trailing_newline=trailing_newline,
        )


def _json_encodable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_encodable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_encodable(item) for item in value]
    return value
