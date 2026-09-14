"""Universal Agent CLI parser package.

The parser is split by responsibility so Golden Path and advanced command
changes stay isolated:

- ``golden_path``: init, run, session, config, profile, doctor
- ``advanced``: observability, serve, tui, ecosystem, catalog, chat, memory
- ``distributed``: local distributed runtime primitives
- ``eval``: evaluation harness commands
- ``kubernetes``: re-exports the Kubernetes Domain CLI parser
"""

from __future__ import annotations

import argparse

from universal_agent.domains.kubernetes.cli_parser import (
    LOCAL_PROFILE_NAME,
    add_kubernetes_command,
)
from universal_agent_cli.parser.advanced import (
    add_catalog_parsers,
    add_chat_parser,
    add_ecosystem_parser,
    add_memory_parser,
    add_observability_parsers,
    add_serve_parser,
    add_tui_parser,
)
from universal_agent_cli.parser.distributed import add_distributed_parser
from universal_agent_cli.parser.eval import add_eval_parser
from universal_agent_cli.parser.golden_path import (
    add_config_parser,
    add_doctor_parser,
    add_init_parser,
    add_profile_parser,
    add_run_parser,
    add_session_parser,
)

from ._helpers import (
    add_evaluation_selector_arguments,
    add_output_argument,
)

GOLDEN_PATH_COMMANDS = (
    "init",
    "run",
    "session",
    "config",
    "profile",
    "doctor",
)

__all__ = [
    "GOLDEN_PATH_COMMANDS",
    "LOCAL_PROFILE_NAME",
    "add_evaluation_selector_arguments",
    "add_kubernetes_command",
    "add_output_argument",
    "build_parser",
]


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog or "agent",
        usage="%(prog)s [options] {init,doctor,run,session,config,profile|advanced...}",
        description=(
            "Universal Agent CLI (also installed as `ua`). "
            "Golden path: init -> doctor -> run -> session. "
            "All other commands are advanced/developer commands."
        ),
        epilog=(
            "Golden Path commands:\n"
            "  init      Create ./universal-agent/profile.json + config.json\n"
            "  doctor    Check config/model/runtime and print fixes\n"
            "  run       Run one Agent goal and create a Session\n"
            "  session   list | show | explain | resume | cancel\n"
            "  config    Show active config without secret values\n"
            "  profile   list | show configured profiles\n\n"
            "Advanced / experimental commands remain available: serve, kubernetes, "
            "tui, eval, ecosystem, distributed, domain-packages, memory, policies, "
            "evaluators, audit, repair, and observability commands."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile-config",
        help="Load an Agent Profile JSON config before dispatching the command.",
    )
    parser.add_argument(
        "--api-url",
        help="Forward supported commands to a running agentd Runtime API.",
    )
    parser.add_argument("--api-token", help="Bearer token for --api-url requests.")
    parser.add_argument(
        "--api-token-env",
        help="Environment variable containing the bearer token for --api-url requests.",
    )
    parser.add_argument(
        "--api-timeout-seconds",
        type=float,
        help=(
            "Per-request timeout for --api-url calls. Long-running commands "
            "(run/kubernetes/eval) default to 900 seconds; everything else to 30."
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{init,doctor,run,session,config,profile|advanced...}",
    )

    # Golden path commands first, so `agent --help` reads top-down:
    # init -> run -> session -> config -> profile -> doctor.
    add_init_parser(commands)
    add_run_parser(commands)
    add_session_parser(commands)
    add_config_parser(commands)
    add_profile_parser(commands)
    add_doctor_parser(commands)

    add_observability_parsers(commands)
    add_distributed_parser(commands)
    add_serve_parser(commands)
    add_kubernetes_command(commands)
    add_tui_parser(commands)
    add_ecosystem_parser(commands)
    add_eval_parser(commands)
    add_catalog_parsers(commands)
    add_chat_parser(commands)
    add_memory_parser(commands)

    return parser
