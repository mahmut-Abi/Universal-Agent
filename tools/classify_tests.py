from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

CONTRACT_PATTERNS: list[tuple[str, int]] = [
    (r"\bto_json\s*\(", 2),
    (r"\bfrom_json\s*\(", 2),
    (r"\bdecode_|encode_", 2),
    (r"\bto_projection\s*\(", 2),
    (r"\b[a-z_]+_body\s*\(", 2),
    (r"\bRuntimeEventBatch\s*\(", 2),
    (
        r"\bSessionView\s*\(|\bRuntimeEventView\s*\(|\bEvidenceView\s*\("
        r"|\bTaskView\s*\(|\bEvaluationView\s*\(",
        2,
    ),
    (r"\bread_json\s*\(", 2),
    (r"assert_json_equal", 2),
    (r"\.payload\b", 1),
    (r"representations\.", 1),
    (r"==\s*\{", 1),
    (r"==\s*\[", 1),
    (r"assert\s+(payload|response|result|output|body|event|view|views|batch|summary)\b", 1),
    (r"round[-_]?trip", 2),
    (r"json\.loads|json\.dumps", 1),
    (r"model_dump|to_dict|as_dict", 1),
]

BEHAVIOR_PATTERNS: list[tuple[str, int]] = [
    (r"\brun_goal\s*\(", 2),
    (r"\bstatus\s*==\s*[\"'](completed|failed|running|recover|finish|ask_user|waiting)[\"']", 2),
    (r"\bGoal\s*\(", 1),
    (r"\bTask\s*\(", 1),
    (r"\bWorldModel\b|world_model", 2),
    (r"\bPolicy(Engine|Result)\b|policy\b", 1),
    (r"\b(recover|RecoveryManager)\b", 2),
    (r"\b(Evaluator|EvaluationResult|evaluate)\b", 2),
    (r"\bDecision(Engine)?\b|decision\b", 1),
    (r"\bObservation\b|Evidence\b", 2),
    (r"\bStateStore\b|session_store|state_store", 2),
    (r"\bconfirm(ation)?_required\b|ConfirmationRequired", 2),
    (r"session_id|SessionId", 1),
    (r"\bagent\.(run|execute)|runtime\.(run|execute)", 2),
    (r"service\.", 1),
    (r"await\s+\w+\.run\b", 1),
    (r"SessionSnapshot|snapshot\b", 1),
    (r"assert\s+\w*\.?status\b", 1),
    (r"\bdiagnos|remediat|verify\b", 1),
]

NAME_CONTRACT = re.compile(
    r"(payload|view|serial|json|encode|decode|round_trip|schema|body|projection|to_json|from_json)"
)
NAME_BEHAVIOR = re.compile(
    r"(behavior|e2e|end_to_end|complete|recover|policy|verify|remediat|diagnos)"
)


def _score(text: str, patterns: list[tuple[str, int]]) -> int:
    return sum(weight for pat, weight in patterns if re.search(pat, text))


def classify(func_name: str, body: str) -> tuple[str, int, int]:
    contract = _score(body, CONTRACT_PATTERNS)
    behavior = _score(body, BEHAVIOR_PATTERNS)
    name = func_name
    if NAME_CONTRACT.search(name):
        contract += 2
    if NAME_BEHAVIOR.search(name):
        behavior += 2
    if contract > behavior and contract >= 2:
        return "contract", contract, behavior
    if behavior > contract and behavior >= 2:
        return "behavior", contract, behavior
    if contract >= 2:
        return "contract", contract, behavior
    if behavior >= 2:
        return "behavior", contract, behavior
    return "unit", contract, behavior


def _module_docstring_end(lines: list[str]) -> int:
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "" or stripped.startswith("from __future__"):
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                return idx + 1
            for j in range(idx + 1, len(lines)):
                if '"""' in lines[j] or "'''" in lines[j]:
                    return j + 1
            return idx + 1
        return idx
    return 0


def process_file(path: Path, dry_run: bool, stats: dict) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    inserts: list[tuple[int, str]] = []
    needs_pytest = "import pytest" not in source

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        body_lines = source.splitlines()[node.lineno - 1 : node.end_lineno]
        body_text = "\n".join(body_lines)
        if re.search(r"pytest.mark.(behavior|contract|unit)", body_text):
            stats["skipped_existing"] += 1
            continue
        category, _contract, _behavior = classify(node.name, body_text)
        stats[category] += 1
        stats["files"].setdefault(str(path), {})[node.name] = category
        inserts.append((node.lineno, category))

    if dry_run:
        return

    lines = source.splitlines(keepends=True)
    for lineno, category in sorted(inserts, reverse=True):
        indent_match = re.match(r"\s*", lines[lineno - 1])
        indent = indent_match.group(0) if indent_match else ""
        lines.insert(lineno - 1, f"{indent}@pytest.mark.{category}\n")
    if needs_pytest:
        insert_at = _module_docstring_end(lines)
        lines.insert(insert_at, "import pytest\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="tests")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    stats: dict = {
        "behavior": 0,
        "contract": 0,
        "unit": 0,
        "skipped_existing": 0,
        "files": {},
    }
    root = Path(args.root)
    for path in sorted(root.rglob("test_*.py")):
        process_file(path, args.dry_run, stats)

    total = stats["behavior"] + stats["contract"] + stats["unit"]
    print(f"total test functions scanned: {total}")
    print(f"  behavior : {stats['behavior']}")
    print(f"  contract : {stats['contract']}")
    print(f"  unit     : {stats['unit']}")
    print(f"  skipped (already marked): {stats['skipped_existing']}")
    if total:
        print(f"behavior share : {stats['behavior'] / total * 100:.1f}%")
        print(f"contract share : {stats['contract'] / total * 100:.1f}%")
        print(f"unit share     : {stats['unit'] / total * 100:.1f}%")
        behavior_plus_contract = stats["behavior"] + stats["contract"]
        if behavior_plus_contract:
            print(
                f"of (behavior+contract): "
                f"behavior {stats['behavior'] / behavior_plus_contract * 100:.1f}% / "
                f"contract {stats['contract'] / behavior_plus_contract * 100:.1f}%"
            )
    if args.report:
        Path(args.report).write_text(json.dumps(stats["files"], indent=2), encoding="utf-8")
        print(f"per-file report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
