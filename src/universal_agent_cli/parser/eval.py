"""Evaluation command parser."""

from __future__ import annotations

import argparse

from universal_agent_cli.parser._helpers import add_evaluation_selector_arguments


def add_eval_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    evaluate = commands.add_parser("eval")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)

    eval_list = eval_commands.add_parser("list")
    eval_list.add_argument("profile")
    eval_list.add_argument("--suite", default="local evaluation suite")
    eval_list.add_argument("--suite-file")
    add_evaluation_selector_arguments(eval_list)

    eval_run = eval_commands.add_parser("run")
    eval_run.add_argument("profile")
    eval_run.add_argument("--suite", default="local evaluation suite")
    eval_run.add_argument("--suite-file")
    eval_run.add_argument("--report-dir")
    eval_run.add_argument("--format", choices=("json", "junit"), default="json")
    eval_run.add_argument("--min-pass-rate", type=float)
    eval_run.add_argument("--min-goal-completion-rate", type=float)
    eval_run.add_argument("--min-task-success-rate", type=float)
    eval_run.add_argument("--min-action-success-rate", type=float)
    eval_run.add_argument("--max-tool-failure-rate", type=float)
    eval_run.add_argument("--max-policy-denial-rate", type=float)
    eval_run.add_argument("--max-average-recoveries", type=float)
    eval_run.add_argument("--max-human-intervention-rate", type=float)
    eval_run.add_argument("--max-average-actions", type=float)
    eval_run.add_argument("--max-average-active-resource-locks", type=float)
    eval_run.add_argument("--max-average-duration-ms", type=float)
    eval_run.add_argument("--max-average-model-calls", type=float)
    eval_run.add_argument("--max-average-model-tokens", type=float)
    eval_run.add_argument("--max-resource-conflict-rate", type=float)
    eval_run.add_argument("--max-total-model-cost-micros", type=int)
    eval_run.add_argument("--fail-on-fail", action="store_true")
    add_evaluation_selector_arguments(eval_run)

    eval_replay = eval_commands.add_parser("replay")
    eval_replay.add_argument("profile")
    eval_replay.add_argument("--suite", default="local evaluation suite")
    eval_replay.add_argument("--suite-file")
    eval_replay.add_argument("--recording-dir", required=True)
    eval_replay.add_argument("--update", action="store_true")
    eval_replay.add_argument("--fail-on-fail", action="store_true")
    add_evaluation_selector_arguments(eval_replay)

    eval_recordings = eval_commands.add_parser("recordings")
    eval_recordings.add_argument("--recording-dir", required=True)

    eval_compare = eval_commands.add_parser("compare")
    eval_compare.add_argument("expected")
    eval_compare.add_argument("actual")
    eval_compare.add_argument("--fail-on-fail", action="store_true")

    eval_reports = eval_commands.add_parser("reports")
    eval_reports.add_argument("--report-dir", required=True)

    eval_console = eval_commands.add_parser("console")
    eval_console.add_argument("--report-dir", required=True)
    eval_console.add_argument("--format", choices=("html", "text"), default="html")

    eval_datasets = eval_commands.add_parser("datasets")
    eval_datasets.add_argument("--dataset-dir", required=True)
    eval_datasets.add_argument("--tag")
    eval_datasets.add_argument("--domain")
    eval_datasets.add_argument("--verify", action="store_true")

    eval_dataset = eval_commands.add_parser("dataset")
    eval_dataset.add_argument("name")
    eval_dataset.add_argument("version", nargs="?")
    eval_dataset.add_argument("--dataset-dir", required=True)
