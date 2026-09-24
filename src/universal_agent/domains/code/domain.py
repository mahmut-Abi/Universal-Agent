"""Code domain: command execution + git operations scoped to the workspace."""

from __future__ import annotations

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    PolicyEffect,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.code.backend import ShellBackend
from universal_agent.evaluation import CriteriaEvaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.tools import Tool
from universal_agent.world import FactWorldUpdater, WorldUpdater

_CODE_VERSION = "0.1.0"


class RunCommandTool:
    def __init__(self, backend: ShellBackend) -> None:
        self.definition = ToolDefinition(
            name="code_run_command",
            description="Execute a shell command in the workspace (build, test, lint, etc.)",
            capabilities=("run_command",),
            required_arguments=("command",),
            side_effect=SideEffect.REVERSIBLE,
            risk=RiskLevel.HIGH,
            argument_schema=immutable_json(
                {
                    "required": ["command"],
                    "properties": {
                        "command": {"type": "string", "minLength": 1},
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 300,
                        },
                    },
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return immutable_json({"error": "command is required"})
        timeout = arguments.get("timeout_seconds")
        timeout_val = int(timeout) if isinstance(timeout, (int, float)) else None
        result = await self._backend.run(command, timeout_seconds=timeout_val)
        return immutable_json(
            {
                "command": command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": result.timed_out,
            }
        )


class GitStatusTool:
    def __init__(self, backend: ShellBackend) -> None:
        self.definition = ToolDefinition(
            name="code_git_status",
            description="Get git status of the workspace",
            capabilities=("git_status",),
            required_arguments=(),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            argument_schema=immutable_json({"properties": {}, "additionalProperties": False}),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        status_result = await self._backend.git("status", "--porcelain=v1")
        branch_result = await self._backend.git("branch", "--show-current")
        return immutable_json(
            {
                "branch": branch_result.stdout.strip(),
                "status": status_result.stdout.strip(),
                "clean": not status_result.stdout.strip(),
            }
        )


class GitDiffTool:
    def __init__(self, backend: ShellBackend) -> None:
        self.definition = ToolDefinition(
            name="code_git_diff",
            description="Get git diff of the workspace (staged + unstaged)",
            capabilities=("git_diff",),
            required_arguments=(),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            argument_schema=immutable_json(
                {
                    "properties": {
                        "staged": {
                            "type": "boolean",
                            "description": "Show staged diff only",
                        }
                    },
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        staged = arguments.get("staged", False)
        args = ["diff", "--cached"] if staged else ["diff"]
        result = await self._backend.git(*args)
        return immutable_json({"diff": result.stdout, "exit_code": result.exit_code})


class GitLogTool:
    def __init__(self, backend: ShellBackend) -> None:
        self.definition = ToolDefinition(
            name="code_git_log",
            description="Get git log of the workspace",
            capabilities=("git_log",),
            required_arguments=(),
            side_effect=SideEffect.NONE,
            risk=RiskLevel.LOW,
            argument_schema=immutable_json(
                {
                    "properties": {
                        "count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                        }
                    },
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        count = arguments.get("count", 10)
        if not isinstance(count, int) or count < 1:
            count = 10
        count = min(count, 50)
        result = await self._backend.git("log", "--oneline", f"-{count}")
        return immutable_json({"log": result.stdout.strip()})


class GitCommitTool:
    def __init__(self, backend: ShellBackend) -> None:
        self.definition = ToolDefinition(
            name="code_git_commit",
            description="Stage all changes and make a git commit",
            capabilities=("git_commit",),
            required_arguments=("message",),
            side_effect=SideEffect.REVERSIBLE,
            risk=RiskLevel.MEDIUM,
            argument_schema=immutable_json(
                {
                    "required": ["message"],
                    "properties": {"message": {"type": "string", "minLength": 1}},
                    "additionalProperties": False,
                }
            ),
        )
        self._backend = backend

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        message = str(arguments.get("message", "")).strip()
        if not message:
            return immutable_json({"error": "commit message is required"})
        add_result = await self._backend.git("add", "-A")
        if add_result.exit_code != 0:
            return immutable_json({"error": "git add failed", "stderr": add_result.stderr})
        commit_result = await self._backend.git("commit", "-m", message)
        if commit_result.exit_code != 0:
            return immutable_json({"error": "git commit failed", "stderr": commit_result.stderr})
        hash_result = await self._backend.git("rev-parse", "--short", "HEAD")
        return immutable_json(
            {
                "committed": True,
                "message": message,
                "commit_hash": hash_result.stdout.strip(),
            }
        )


class CodeEvidenceExtractor(EvidenceExtractor):
    name = "code-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status.value != "succeeded":
            return ()
        data = context.observation.data
        subject = str(data.get("command", data.get("branch", "code")))
        return tuple(
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                subject,
                key,
                value,
                self.name,
                0.9,
                observed_at=context.observation.observed_at,
            )
            for key, value in data.items()
            if key not in ("stdout", "stderr")
        )


class CodeContextProvider(DomainContextProvider):
    name = "code-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (
            ContextFragment(
                "code.scope",
                "Inspect and operate on the codebase: git operations, build, "
                "test, and lint commands.",
                10,
            ),
        )


class CodeDomain:
    """Code domain: command execution + git operations scoped to the workspace."""

    def __init__(self, backend: ShellBackend) -> None:
        self._backend = backend

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="Domain",
            metadata=DomainMetadata(
                "code",
                _CODE_VERSION,
                "Code agent domain: command execution + git operations",
            ),
            ontology=("Codebase", "Branch", "Commit", "Command"),
            capability_names=(
                "run_command",
                "git_status",
                "git_diff",
                "git_log",
                "git_commit",
            ),
            evaluator_names=("criteria",),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "run_command",
                "Execute a shell command (build, test, lint)",
                CapabilityCategory.MUTATION,
                RiskLevel.HIGH,
            ),
            CapabilityDefinition(
                "git_status",
                "Get git status",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                "git_diff",
                "Get git diff",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                "git_log",
                "Get git log",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
            CapabilityDefinition(
                "git_commit",
                "Stage all changes and make a git commit",
                CapabilityCategory.MUTATION,
                RiskLevel.MEDIUM,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (
            RunCommandTool(self._backend),
            GitStatusTool(self._backend),
            GitDiffTool(self._backend),
            GitLogTool(self._backend),
            GitCommitTool(self._backend),
        )

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "code-git-read-only",
                PolicyEffect.ALLOW,
                "read-only git operations allowed",
                capabilities=("git_status", "git_diff", "git_log"),
            ),
            PolicyRule(
                "code-command-confirmation",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "shell command execution requires confirmation",
                capabilities=("run_command",),
            ),
            PolicyRule(
                "code-git-commit-confirmation",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "git commit requires confirmation",
                capabilities=("git_commit",),
            ),
        )

    def evaluators(self) -> tuple[CriteriaEvaluator, ...]:
        return (CriteriaEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (CodeContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (CodeEvidenceExtractor(),)

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return (
            RecoveryRule(
                "code-timeout-retry",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
                priority=10,
                match_capabilities=(
                    "run_command",
                    "git_status",
                    "git_diff",
                    "git_log",
                ),
            ),
        )

    def task_expanders(self) -> tuple:  # type: ignore[type-arg]
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (FactWorldUpdater(),)

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()
