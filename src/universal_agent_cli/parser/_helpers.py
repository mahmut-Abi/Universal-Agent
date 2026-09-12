"""Shared argument helpers for the Universal Agent CLI parsers."""

from __future__ import annotations

import argparse


def add_output_argument(command: argparse.ArgumentParser, *, default: str = "json") -> None:
    """Standard `--output text|json` flag for session lifecycle commands."""

    command.add_argument(
        "--output",
        choices=("text", "json"),
        default=default,
        help="Human summary (text) or machine JSON (default: json).",
    )


def add_evaluation_selector_arguments(command: argparse.ArgumentParser) -> None:
    """Shared eval scenario selector flags used by list/run/replay."""

    from universal_agent.evaluation.harness import EvaluationScenarioKind

    command.add_argument(
        "--kind",
        action="append",
        choices=tuple(item.value for item in EvaluationScenarioKind),
    )
    command.add_argument("--tag", action="append")
    command.add_argument("--exclude-tag", action="append")
