"""Universal Agent CLI parser package.

The parser is split by responsibility so Golden Path and advanced command
changes stay isolated:

- ``golden_path``: init, run, session, config, profile, doctor
- ``advanced``: observability, serve, tui, ecosystem, catalog, chat, memory
- ``distributed``: local distributed runtime primitives
- ``eval``: evaluation harness commands
- domain-contributed commands (e.g. a domain operator command) join via the
  ``universal_agent.cli_contributions`` entry-point group; this package
  never names a concrete domain.
"""

from __future__ import annotations

import argparse

from universal_agent_cli.contributions import (
    CliDomainContribution,
    load_cli_contributions,
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
    "add_evaluation_selector_arguments",
    "add_output_argument",
    "build_parser",
    "domain_contribution_for_command",
    "load_cli_contributions",
    "local_profile_name",
]


def local_profile_name() -> str:
    """The local/operator default profile name from domain contributions."""

    for contribution in load_cli_contributions():
        if contribution.local_profile_name is not None:
            return contribution.local_profile_name
    return "default"


def __getattr__(name: str) -> object:
    # Compatibility re-export: resolved via domain contributions so this
    # package never names a concrete domain.
    if name == "LOCAL_PROFILE_NAME":
        return local_profile_name()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def domain_contribution_for_command(
    command: str,
    contributions: tuple[CliDomainContribution, ...] | None = None,
) -> CliDomainContribution | None:
    """Find the domain contribution that owns an advanced command name."""

    for contribution in contributions if contributions is not None else load_cli_contributions():
        if contribution.command_name == command:
            return contribution
    return None


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    contributions = load_cli_contributions()
    contributed_commands = ", ".join(
        c.command_name for c in contributions if c.command_name is not None
    )
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
            "Advanced / experimental commands remain available: serve, "
            f"{contributed_commands}, "
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
            "(e.g. run, eval and domain operator commands) default to 900 "
            "seconds; everything else to 30."
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{init,doctor,run,session,config,profile|advanced...}",
    )

    # Golden path commands first, so `agent --help` reads top-down:
    # init -> run -> session -> config -> profile -> doctor.
    add_init_parser(commands, contributions=contributions)
    add_run_parser(commands)
    add_session_parser(commands)
    add_config_parser(commands)
    add_profile_parser(commands)
    add_doctor_parser(commands)

    add_observability_parsers(commands)
    add_distributed_parser(commands)
    add_serve_parser(commands)
    for contribution in contributions:
        if contribution.add_command is not None:
            contribution.add_command(commands)
    add_tui_parser(commands)
    add_ecosystem_parser(commands)
    add_eval_parser(commands)
    add_catalog_parsers(commands)
    add_chat_parser(commands)
    add_memory_parser(commands)

    return parser
