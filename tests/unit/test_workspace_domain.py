"""Unit tests for the Workspace Domain.

Tests cover:
- Domain manifest and capabilities
- Tool execution (inspect, read, search, create, modify)
- Policy enforcement
- Evidence extraction
- World model updates
- Task expansion
- Recovery rules
- Context providers
- Memory records
- Domain loader validation
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from universal_agent.core import (
    ActionId,
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    EvaluationContext,
    EvaluationStatus,
    Goal,
    GoalId,
    Observation,
    ObservationId,
    ObservationStatus,
    PolicyContext,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    SuccessCriterion,
    Task,
    TaskId,
    ToolDefinition,
    immutable_json,
    utc_now,
)
from universal_agent.domain import DomainLoader
from universal_agent.domains.workspace import (
    ALL_CAPABILITIES,
    CREATE_FILE_CAPABILITY,
    DELETE_FILE_CAPABILITY,
    INSPECT_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    MODIFY_FILE_CAPABILITY,
    SEARCH_FILES_CAPABILITY,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
    WorkspaceCompletionEvaluator,
    WorkspaceContextProvider,
    WorkspaceDomain,
    WorkspaceEvidenceExtractor,
    WorkspaceRecoveryRule,
    WorkspaceTaskExpander,
    WorkspaceWorldUpdater,
    workspace_identity,
)
from universal_agent.evidence import Evidence, EvidenceContext
from universal_agent.policy import PolicyEngine
from universal_agent.recovery import FailureCategory, RecoveryStrategy
from universal_agent.world import InMemoryWorldModel

# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def workspace_dir() -> Generator[Path, None, None]:
    """Create a temporary workspace directory."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def workspace_with_files(workspace_dir: Path) -> Path:
    """Create a workspace with some test files."""
    (workspace_dir / "hello.py").write_text("print('hello')\n")
    (workspace_dir / "README.md").write_text("# Test\n")
    (workspace_dir / "data.txt").write_text("line1\nline2\nline3\n")
    return workspace_dir


@pytest.fixture
def domain(workspace_dir: Path) -> WorkspaceDomain:
    """Create a WorkspaceDomain instance."""
    return WorkspaceDomain(workspace_dir)


@pytest.fixture
def domain_with_files(workspace_with_files: Path) -> WorkspaceDomain:
    """Create a WorkspaceDomain with test files."""
    return WorkspaceDomain(workspace_with_files)


# ─── Domain Identity Tests ──────────────────────────────────────────────────


class TestDomainIdentity:
    def test_identity(self) -> None:
        identity = workspace_identity()
        assert identity.name == WORKSPACE_DOMAIN_NAME
        assert identity.version == WORKSPACE_DOMAIN_VERSION

    def test_domain_identity(self, domain: WorkspaceDomain) -> None:
        assert domain.identity.name == WORKSPACE_DOMAIN_NAME
        assert domain.identity.version == WORKSPACE_DOMAIN_VERSION


# ─── Manifest Tests ─────────────────────────────────────────────────────────


class TestManifest:
    def test_manifest_metadata(self, domain: WorkspaceDomain) -> None:
        manifest = domain.manifest
        assert manifest.metadata.name == WORKSPACE_DOMAIN_NAME
        assert manifest.metadata.version == WORKSPACE_DOMAIN_VERSION
        assert "file-operation" in manifest.metadata.description.lower()

    def test_manifest_capabilities(self, domain: WorkspaceDomain) -> None:
        manifest = domain.manifest
        assert set(manifest.capability_names) == set(ALL_CAPABILITIES)

    def test_manifest_evaluators(self, domain: WorkspaceDomain) -> None:
        manifest = domain.manifest
        assert len(manifest.evaluator_names) == 1
        assert "workspace-completion" in manifest.evaluator_names


# ─── Capability Tests ───────────────────────────────────────────────────────


class TestCapabilities:
    def test_capability_count(self, domain: WorkspaceDomain) -> None:
        caps = domain.capabilities()
        assert len(caps) == 6

    def test_observation_capabilities(self, domain: WorkspaceDomain) -> None:
        caps = domain.capabilities()
        obs = [c for c in caps if c.category == CapabilityCategory.OBSERVATION]
        assert len(obs) == 3
        obs_names = {c.name for c in obs}
        assert INSPECT_WORKSPACE_CAPABILITY in obs_names
        assert INSPECT_FILE_CAPABILITY in obs_names
        assert SEARCH_FILES_CAPABILITY in obs_names

    def test_mutation_capabilities(self, domain: WorkspaceDomain) -> None:
        caps = domain.capabilities()
        mut = [c for c in caps if c.category == CapabilityCategory.MUTATION]
        assert len(mut) == 3
        mut_names = {c.name for c in mut}
        assert CREATE_FILE_CAPABILITY in mut_names
        assert MODIFY_FILE_CAPABILITY in mut_names
        assert DELETE_FILE_CAPABILITY in mut_names

    def test_capability_risk_levels(self, domain: WorkspaceDomain) -> None:
        caps = domain.capabilities()
        for cap in caps:
            if cap.category == CapabilityCategory.OBSERVATION:
                assert cap.risk == RiskLevel.LOW
            elif cap.name == DELETE_FILE_CAPABILITY:
                assert cap.risk == RiskLevel.HIGH
            else:
                assert cap.risk == RiskLevel.MEDIUM


# ─── Tool Tests ─────────────────────────────────────────────────────────────


class TestTools:
    def test_tool_count(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        assert len(tools) == 6

    def test_tool_names(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        names = {t.definition.name for t in tools}
        assert "workspace_inspect" in names
        assert "workspace_read_file" in names
        assert "workspace_search" in names
        assert "workspace_create_file" in names
        assert "workspace_modify_file" in names

    def test_tool_capabilities(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        cap_names = {c.name for c in domain.capabilities()}
        for tool in tools:
            for cap in tool.definition.capabilities:
                assert cap in cap_names

    def test_read_only_tools_have_no_side_effect(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        read_only_names = {"workspace_inspect", "workspace_read_file", "workspace_search"}
        read_only = [t for t in tools if t.definition.name in read_only_names]
        for tool in read_only:
            assert tool.definition.side_effect == SideEffect.NONE

    def test_mutation_tools_have_side_effect(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        mutation_names = {"workspace_create_file", "workspace_modify_file"}
        mutations = [t for t in tools if t.definition.name in mutation_names]
        for tool in mutations:
            assert tool.definition.side_effect == SideEffect.REVERSIBLE


# ─── Inspect Workspace Tool Tests ───────────────────────────────────────────


class TestInspectWorkspaceTool:
    @pytest.mark.asyncio
    async def test_inspect_empty_workspace(
        self, domain: WorkspaceDomain, workspace_dir: Path
    ) -> None:
        tools = domain.tools()
        inspect = next(t for t in tools if t.definition.name == "workspace_inspect")
        result = await inspect.execute(immutable_json({}))
        assert result["readable"] is True
        assert result["healthy"] is True
        assert result["file_count"] == 0
        assert result["directory_count"] == 0

    @pytest.mark.asyncio
    async def test_inspect_workspace_with_files(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        inspect = next(t for t in tools if t.definition.name == "workspace_inspect")
        result = await inspect.execute(immutable_json({}))
        assert result["readable"] is True
        assert result["healthy"] is True
        assert result["file_count"] == 3
        files_raw = result.get("files", [])
        assert isinstance(files_raw, list)
        files = [str(f) for f in files_raw]
        assert "hello.py" in files
        assert "README.md" in files
        markers_raw = result.get("project_markers", [])
        assert isinstance(markers_raw, list)
        markers = [str(m) for m in markers_raw]
        assert "README.md" in markers


# ─── Read File Tool Tests ───────────────────────────────────────────────────


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        read_file = next(t for t in tools if t.definition.name == "workspace_read_file")
        result = await read_file.execute(immutable_json({"path": "hello.py"}))
        assert result["readable"] is True
        assert result["healthy"] is True
        assert "print('hello')" in str(result["content"])

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        read_file = next(t for t in tools if t.definition.name == "workspace_read_file")
        result = await read_file.execute(immutable_json({"path": "nonexistent.py"}))
        assert result["readable"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_path_escape(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        read_file = next(t for t in tools if t.definition.name == "workspace_read_file")
        result = await read_file.execute(immutable_json({"path": "../etc/passwd"}))
        assert result["readable"] is False


# ─── Search Tool Tests ──────────────────────────────────────────────────────


class TestSearchTool:
    @pytest.mark.asyncio
    async def test_search_existing_pattern(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        search = next(t for t in tools if t.definition.name == "workspace_search")
        result = await search.execute(immutable_json({"pattern": "hello", "glob": "*.py"}))
        assert int(str(result["match_count"])) >= 1

    @pytest.mark.asyncio
    async def test_search_no_match(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        search = next(t for t in tools if t.definition.name == "workspace_search")
        result = await search.execute(
            immutable_json({"pattern": "nonexistent_xyz", "glob": "*.py"})
        )
        assert result["match_count"] == 0

    @pytest.mark.asyncio
    async def test_search_invalid_regex(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        search = next(t for t in tools if t.definition.name == "workspace_search")
        result = await search.execute(immutable_json({"pattern": "[invalid"}))
        assert "error" in result


# ─── Create File Tool Tests ─────────────────────────────────────────────────


class TestCreateFileTool:
    @pytest.mark.asyncio
    async def test_create_new_file(self, domain: WorkspaceDomain, workspace_dir: Path) -> None:
        tools = domain.tools()
        create = next(t for t in tools if t.definition.name == "workspace_create_file")
        result = await create.execute(
            immutable_json(
                {
                    "path": "new_file.txt",
                    "content": "hello world",
                }
            )
        )
        assert result["created"] is True
        assert result["healthy"] is True
        assert (workspace_dir / "new_file.txt").read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_create_existing_file(self, domain_with_files: WorkspaceDomain) -> None:
        tools = domain_with_files.tools()
        create = next(t for t in tools if t.definition.name == "workspace_create_file")
        result = await create.execute(
            immutable_json(
                {
                    "path": "hello.py",
                    "content": "new content",
                }
            )
        )
        assert result["created"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_in_subdirectory(
        self, domain: WorkspaceDomain, workspace_dir: Path
    ) -> None:
        tools = domain.tools()
        create = next(t for t in tools if t.definition.name == "workspace_create_file")
        result = await create.execute(
            immutable_json(
                {
                    "path": "subdir/nested/file.txt",
                    "content": "nested content",
                }
            )
        )
        assert result["created"] is True
        expected = workspace_dir / "subdir/nested/file.txt"
        assert expected.read_text() == "nested content"

    @pytest.mark.asyncio
    async def test_create_path_escape(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        create = next(t for t in tools if t.definition.name == "workspace_create_file")
        result = await create.execute(
            immutable_json(
                {
                    "path": "../escape.txt",
                    "content": "bad",
                }
            )
        )
        assert result["created"] is False


# ─── Modify File Tool Tests ─────────────────────────────────────────────────


class TestModifyFileTool:
    @pytest.mark.asyncio
    async def test_modify_existing_file(
        self,
        domain_with_files: WorkspaceDomain,
        workspace_with_files: Path,
    ) -> None:
        tools = domain_with_files.tools()
        modify = next(t for t in tools if t.definition.name == "workspace_modify_file")
        result = await modify.execute(
            immutable_json(
                {
                    "path": "hello.py",
                    "content": "print('modified')",
                }
            )
        )
        assert result["modified"] is True
        assert result["healthy"] is True
        assert (workspace_with_files / "hello.py").read_text() == ("print('modified')")

    @pytest.mark.asyncio
    async def test_modify_nonexistent_file(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        modify = next(t for t in tools if t.definition.name == "workspace_modify_file")
        result = await modify.execute(
            immutable_json(
                {
                    "path": "nonexistent.txt",
                    "content": "new",
                }
            )
        )
        assert result["modified"] is False
        assert "error" in result


# ─── Delete File Tool Tests ─────────────────────────────────────────────


class TestDeleteFileTool:
    @pytest.mark.asyncio
    async def test_delete_existing_file(
        self, domain_with_files: WorkspaceDomain, workspace_with_files: Path
    ) -> None:
        tools = domain_with_files.tools()
        delete = next(t for t in tools if t.definition.name == "workspace_delete_file")
        result = await delete.execute(immutable_json({"path": "hello.py"}))
        assert result["deleted"] is True
        assert not (workspace_with_files / "hello.py").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        delete = next(t for t in tools if t.definition.name == "workspace_delete_file")
        result = await delete.execute(immutable_json({"path": "ghost.txt"}))
        assert result["deleted"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_path_escape(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        delete = next(t for t in tools if t.definition.name == "workspace_delete_file")
        result = await delete.execute(immutable_json({"path": "../outside.txt"}))
        assert result["deleted"] is False

    def test_delete_tool_is_destructive_high_risk(self, domain: WorkspaceDomain) -> None:
        tools = domain.tools()
        delete = next(t for t in tools if t.definition.name == "workspace_delete_file")
        assert delete.definition.side_effect == SideEffect.DESTRUCTIVE
        assert delete.definition.risk == RiskLevel.HIGH


# ─── Evaluator Tests ────────────────────────────────────────────────────────


class TestEvaluator:
    def test_evaluator_name(self) -> None:
        evaluator = WorkspaceCompletionEvaluator()
        assert evaluator.name == "workspace-completion"

    def test_evaluator_completes_when_criteria_met(self) -> None:
        evaluator = WorkspaceCompletionEvaluator()
        goal = Goal("Create file", (SuccessCriterion("created", True),))
        task = Task("Create file", ("created",))
        observation = Observation(
            id=ObservationId("obs-1"),
            action_id=ActionId("action-1"),
            task_id=TaskId("task-1"),
            source="test",
            status=ObservationStatus.SUCCEEDED,
            data=immutable_json({}),
            observed_at=utc_now(),
        )
        context = EvaluationContext(
            goal=goal,
            task=task,
            observation=observation,
            satisfied_criteria=immutable_json({"created": True}),
        )
        result = evaluator.evaluate(context)
        assert result.status == EvaluationStatus.COMPLETED
        assert result.task_completed is True
        assert result.goal_completed is True

    def test_evaluator_incomplete_when_criteria_not_met(self) -> None:
        evaluator = WorkspaceCompletionEvaluator()
        goal = Goal("Create file", (SuccessCriterion("created", True),))
        task = Task("Create file", ("created",))
        observation = Observation(
            id=ObservationId("obs-1"),
            action_id=ActionId("action-1"),
            task_id=TaskId("task-1"),
            source="test",
            status=ObservationStatus.SUCCEEDED,
            data=immutable_json({}),
            observed_at=utc_now(),
        )
        context = EvaluationContext(
            goal=goal,
            task=task,
            observation=observation,
            satisfied_criteria=immutable_json({}),
        )
        result = evaluator.evaluate(context)
        assert result.status == EvaluationStatus.INCOMPLETE


# ─── Evidence Extractor Tests ───────────────────────────────────────────────


class TestEvidenceExtractor:
    def test_extractor_name(self) -> None:
        extractor = WorkspaceEvidenceExtractor()
        assert extractor.name == "workspace-evidence"

    def test_extractor_returns_empty_on_failure(self) -> None:
        extractor = WorkspaceEvidenceExtractor()
        observation = Observation(
            id=ObservationId("obs-1"),
            action_id=ActionId("action-1"),
            task_id=TaskId("task-1"),
            source="test",
            status=ObservationStatus.FAILED,
            data=immutable_json({}),
            observed_at=utc_now(),
        )
        context = EvidenceContext(
            session_id=SessionId("session-1"),
            task=Task("test", ("test",)),
            observation=observation,
        )
        evidence = extractor.extract(context)
        assert len(evidence) == 0

    def test_extractor_extracts_evidence_on_success(self) -> None:
        extractor = WorkspaceEvidenceExtractor()
        observation = Observation(
            id=ObservationId("obs-1"),
            action_id=ActionId("action-1"),
            task_id=TaskId("task-1"),
            source="workspace_inspect",
            status=ObservationStatus.SUCCEEDED,
            data=immutable_json(
                {
                    "resource": "workspace",
                    "healthy": True,
                    "file_count": 5,
                }
            ),
            observed_at=utc_now(),
        )
        context = EvidenceContext(
            session_id=SessionId("session-1"),
            task=Task("test", ("test",)),
            observation=observation,
        )
        evidence = extractor.extract(context)
        assert len(evidence) >= 2
        claims = {e.claim for e in evidence}
        assert "healthy" in claims
        assert "file_count" in claims


# ─── World Updater Tests ────────────────────────────────────────────────────


class TestWorldUpdater:
    def test_updater_name(self) -> None:
        updater = WorkspaceWorldUpdater()
        assert updater.name == "workspace-world"

    def test_updater_applies_known_claims(self) -> None:
        updater = WorkspaceWorldUpdater()
        model = InMemoryWorldModel()
        evidence = Evidence(
            session_id=SessionId("session-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            observation_id=ObservationId("obs-1"),
            subject="workspace",
            claim="healthy",
            value=True,
            source="test",
        )
        result = updater.apply(model, evidence)
        assert result is True

    def test_updater_rejects_unknown_claims(self) -> None:
        updater = WorkspaceWorldUpdater()
        model = InMemoryWorldModel()
        evidence = Evidence(
            session_id=SessionId("session-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            observation_id=ObservationId("obs-1"),
            subject="workspace",
            claim="unknown_claim",
            value=True,
            source="test",
        )
        result = updater.apply(model, evidence)
        assert result is False


# ─── Task Expander Tests ────────────────────────────────────────────────────


class TestTaskExpander:
    def test_expander_name(self) -> None:
        expander = WorkspaceTaskExpander()
        assert expander.name == "workspace-workflow"

    def test_expander_capability_names(self) -> None:
        expander = WorkspaceTaskExpander()
        assert INSPECT_WORKSPACE_CAPABILITY in expander.capability_names
        assert CREATE_FILE_CAPABILITY in expander.capability_names


# ─── Recovery Rules Tests ───────────────────────────────────────────────────


class TestRecoveryRules:
    def test_recovery_rules_count(self) -> None:
        recovery = WorkspaceRecoveryRule()
        rules = recovery.rules
        assert len(rules) == 3

    def test_timeout_retry_rule(self) -> None:
        recovery = WorkspaceRecoveryRule()
        timeout_rule = next(r for r in recovery.rules if r.name == "workspace-timeout-retry")
        assert FailureCategory.TIMEOUT in timeout_rule.categories
        assert timeout_rule.strategy == RecoveryStrategy.RETRY_ACTION
        assert timeout_rule.max_attempts == 2

    def test_file_exists_recovery(self) -> None:
        recovery = WorkspaceRecoveryRule()
        exists_rule = next(r for r in recovery.rules if r.name == "workspace-file-exists")
        assert exists_rule.strategy == RecoveryStrategy.ALTERNATIVE_CAPABILITY
        assert exists_rule.capability == MODIFY_FILE_CAPABILITY

    def test_file_not_found_recovery(self) -> None:
        recovery = WorkspaceRecoveryRule()
        not_found_rule = next(r for r in recovery.rules if r.name == "workspace-file-not-found")
        assert not_found_rule.strategy == RecoveryStrategy.ALTERNATIVE_CAPABILITY
        assert not_found_rule.capability == CREATE_FILE_CAPABILITY


# ─── Context Provider Tests ─────────────────────────────────────────────────


class TestContextProvider:
    def test_provider_name(self, workspace_dir: Path) -> None:
        provider = WorkspaceContextProvider(workspace_dir)
        assert provider.name == "workspace-context"

    def test_provider_provides_fragments(self, workspace_dir: Path) -> None:
        provider = WorkspaceContextProvider(workspace_dir)
        state = AgentState(
            session_id=SessionId("session-1"),
            goal=Goal("test", ()),
            current_task=Task("test", ()),
        )
        fragments = provider.provide(state)
        assert len(fragments) == 1
        assert "Workspace:" in fragments[0].content
        assert "Files:" in fragments[0].content


# ─── Memory Tests ───────────────────────────────────────────────────────────


class TestMemory:
    def test_memories_count(self, domain: WorkspaceDomain) -> None:
        memories = domain.memories()
        assert len(memories) == 2

    def test_semantic_memory(self, domain: WorkspaceDomain) -> None:
        memories = domain.memories()
        semantic = [m for m in memories if m.kind.value == "semantic"]
        assert len(semantic) == 1
        assert "workspace" in semantic[0].subject

    def test_procedural_memory(self, domain: WorkspaceDomain) -> None:
        memories = domain.memories()
        procedural = [m for m in memories if m.kind.value == "procedural"]
        assert len(procedural) == 1
        assert "create" in procedural[0].subject


# ─── Policy Tests ───────────────────────────────────────────────────────────


class TestPolicies:
    def test_policies_count(self, domain: WorkspaceDomain) -> None:
        policies = domain.policies()
        assert len(policies) == 3

    def test_read_allowed(self, domain: WorkspaceDomain) -> None:
        policies = domain.policies()
        engine = PolicyEngine(policies)
        context = PolicyContext(
            session_id=SessionId("session-1"),
            goal_id=GoalId("goal-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            capability=CapabilityDefinition(
                INSPECT_WORKSPACE_CAPABILITY,
                "test",
                CapabilityCategory.OBSERVATION,
            ),
            tool=ToolDefinition(
                "workspace_inspect",
                "test",
                (INSPECT_WORKSPACE_CAPABILITY,),
            ),
            target=None,
            arguments=immutable_json({}),
            confirmed=False,
        )
        result = engine.check(context)
        assert result.effect == PolicyEffect.ALLOW

    def test_mutation_allowed(self, domain: WorkspaceDomain) -> None:
        policies = domain.policies()
        engine = PolicyEngine(policies)
        context = PolicyContext(
            session_id=SessionId("session-1"),
            goal_id=GoalId("goal-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            capability=CapabilityDefinition(
                CREATE_FILE_CAPABILITY,
                "test",
                CapabilityCategory.MUTATION,
            ),
            tool=ToolDefinition(
                "workspace_create_file",
                "test",
                (CREATE_FILE_CAPABILITY,),
            ),
            target=None,
            arguments=immutable_json({}),
            confirmed=False,
        )
        result = engine.check(context)
        assert result.effect == PolicyEffect.ALLOW

    def test_delete_requires_confirmation(self, domain: WorkspaceDomain) -> None:
        policies = domain.policies()
        engine = PolicyEngine(policies)
        context = PolicyContext(
            session_id=SessionId("session-1"),
            goal_id=GoalId("goal-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            capability=CapabilityDefinition(
                DELETE_FILE_CAPABILITY,
                "test",
                CapabilityCategory.MUTATION,
                RiskLevel.HIGH,
            ),
            tool=ToolDefinition(
                "workspace_delete_file",
                "test",
                (DELETE_FILE_CAPABILITY,),
                side_effect=SideEffect.DESTRUCTIVE,
                risk=RiskLevel.HIGH,
            ),
            target=None,
            arguments=immutable_json({}),
            confirmed=False,
        )
        result = engine.check(context)
        assert result.effect == PolicyEffect.REQUIRE_CONFIRMATION

    def test_delete_allowed_after_confirmation(self, domain: WorkspaceDomain) -> None:
        policies = domain.policies()
        engine = PolicyEngine(policies)
        context = PolicyContext(
            session_id=SessionId("session-1"),
            goal_id=GoalId("goal-1"),
            task_id=TaskId("task-1"),
            action_id=ActionId("action-1"),
            capability=CapabilityDefinition(
                DELETE_FILE_CAPABILITY,
                "test",
                CapabilityCategory.MUTATION,
                RiskLevel.HIGH,
            ),
            tool=ToolDefinition(
                "workspace_delete_file",
                "test",
                (DELETE_FILE_CAPABILITY,),
                side_effect=SideEffect.DESTRUCTIVE,
                risk=RiskLevel.HIGH,
            ),
            target=None,
            arguments=immutable_json({}),
            confirmed=True,
        )
        result = engine.check(context)
        assert result.effect == PolicyEffect.ALLOW


# ─── Domain Loader Tests ────────────────────────────────────────────────────


class TestDomainLoader:
    def test_loader_validates_domain(self, domain: WorkspaceDomain) -> None:
        loader = DomainLoader()
        active = loader.load(domain)
        assert active.identity.name == WORKSPACE_DOMAIN_NAME
        assert len(active.capabilities) == 6
        assert len(active.tools) == 6
        assert len(active.evaluators) == 1
        assert len(active.policies) == 3
        assert len(active.recovery_rules) == 3
        assert len(active.memories) == 2


# ─── Integration Tests ──────────────────────────────────────────────────────


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_workflow_create_file(
        self, domain: WorkspaceDomain, workspace_dir: Path
    ) -> None:
        """Test a full workflow: inspect -> create -> verify."""
        tools = domain.tools()
        inspect = next(t for t in tools if t.definition.name == "workspace_inspect")
        create = next(t for t in tools if t.definition.name == "workspace_create_file")
        read_file = next(t for t in tools if t.definition.name == "workspace_read_file")

        # Step 1: Inspect workspace
        state = await inspect.execute(immutable_json({}))
        assert state["file_count"] == 0

        # Step 2: Create file
        result = await create.execute(
            immutable_json(
                {
                    "path": "test.py",
                    "content": "print('test')",
                }
            )
        )
        assert result["created"] is True

        # Step 3: Verify file exists
        content = await read_file.execute(immutable_json({"path": "test.py"}))
        assert content["readable"] is True
        assert "print('test')" in str(content["content"])

        # Step 4: Inspect again
        state2 = await inspect.execute(immutable_json({}))
        assert state2["file_count"] == 1

    @pytest.mark.asyncio
    async def test_full_workflow_modify_file(
        self,
        domain_with_files: WorkspaceDomain,
        workspace_with_files: Path,
    ) -> None:
        """Test a full workflow: read -> modify -> verify."""
        tools = domain_with_files.tools()
        read_file = next(t for t in tools if t.definition.name == "workspace_read_file")
        modify = next(t for t in tools if t.definition.name == "workspace_modify_file")

        # Step 1: Read original
        original = await read_file.execute(immutable_json({"path": "hello.py"}))
        assert "print('hello')" in str(original["content"])

        # Step 2: Modify
        result = await modify.execute(
            immutable_json(
                {
                    "path": "hello.py",
                    "content": "print('modified')",
                }
            )
        )
        assert result["modified"] is True

        # Step 3: Verify modification
        modified = await read_file.execute(immutable_json({"path": "hello.py"}))
        assert "print('modified')" in str(modified["content"])
