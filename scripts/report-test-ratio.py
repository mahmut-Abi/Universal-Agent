#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

TRACKED = ("behavior", "contract", "unit")
TARGET_BEHAVIOR_RATIO = 0.30


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("tests")
    counts = test_marker_counts(root)
    total = sum(counts.values())
    behavior_ratio = counts["behavior"] / total if total else 0.0
    print("Test ratio report")
    print(f"  behavior: {counts['behavior']}")
    print(f"  contract : {counts['contract']}")
    print(f"  unit     : {counts['unit']}")
    print(f"  total    : {total}")
    print(f"  behavior ratio: {behavior_ratio:.1%}")
    print(f"  short-term target: behavior >= {TARGET_BEHAVIOR_RATIO:.0%}")
    return 0


def test_marker_counts(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter({name: 0 for name in TRACKED})
    for path in sorted(root.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_markers = set(_module_pytestmark_names(tree))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            markers = module_markers | set(_decorator_marker_names(node.decorator_list))
            matched = [name for name in TRACKED if name in markers]
            if matched:
                for name in matched:
                    counts[name] += 1
            else:
                counts["unit"] += 1
    return counts


def _module_pytestmark_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        assigns_pytestmark = any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        )
        if not assigns_pytestmark:
            continue
        value = statement.value
        items = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
        for item in items:
            marker = _marker_name(item)
            if marker:
                names.append(marker)
    return names


def _decorator_marker_names(decorators: list[ast.expr]) -> list[str]:
    names: list[str] = []
    for decorator in decorators:
        marker = _marker_name(decorator)
        if marker:
            names.append(marker)
    return names


def _marker_name(node: ast.AST) -> str | None:
    candidate = node.func if isinstance(node, ast.Call) else node
    if not isinstance(candidate, ast.Attribute):
        return None
    if not isinstance(candidate.value, ast.Attribute):
        return None
    if not isinstance(candidate.value.value, ast.Name):
        return None
    if candidate.value.value.id != "pytest" or candidate.value.attr != "mark":
        return None
    return candidate.attr


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
