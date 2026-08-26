from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from universal_agent.capability import CapabilityUnavailableError, UnknownCapabilityError
from universal_agent.context import BasicContextCompiler, ContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    CapabilityDefinition,
    CapabilityInputContract,
    Decision,
    DecisionType,
    ErrorCode,
    EvaluationStatus,
    ExecutionResult,
    ExecutionStatus,
    Goal,
    GoalStatus,
    JsonMapping,
    ObservationStatus,
    PendingAction,
    RuntimeEvent,
    SessionId,
    Task,
    TaskStatus,
    immutable_json,
    new_session_id,
    validate_argument_contract,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.memory import MemoryKind, MemoryRecord, RetrievalRequest
from universal_agent.model import ModelAdapter, model_usage
from universal_agent.recovery import Failure, RecoveryStrategy, classify_failure
from universal_agent.runtime.actions import (
    ActionExecutor,
    ActionObserved,
    ActionRejected,
    ConfirmationRequired,
)
from universal_agent.runtime.events import EventSink
from universal_agent.runtime.processing import ObservationProcessor, ObservationRoutingError
from universal_agent.runtime.session import (
    SessionHydrationError,
    SessionRuntimeState,
    hydrate_session,
    mark_current_task,
    start_session,
)
from universal_agent.runtime.transitions import (
    Transition,
    build_result,
    fail,
    finish,
)
from universal_agent.runtime.transitions import (
    cancel as cancel_transition,
)
from universal_agent.runtime.transitions import pause as pause_transition
from universal_agent.security import SecretProvider, SecretResolutionReport
from universal_agent.state import (
    SessionSnapshot,
    SessionStore,
    StateEventCommitter,
    session_from_state,
)


class AgentRuntime:
    """Drives the session loop.

    Owns the state store and the event sink. Action execution, observation
    processing and terminal transitions live in their own modules; this class
    only decides the order in which they run.
    """

    def __init__(
        self,
        *,
        model: ModelAdapter,
        state_store: SessionStore,
        components: RuntimeComponents,
        event_sink: EventSink,
        context_compiler: ContextCompiler | None = None,
        max_iterations: int = 20,
        max_recovery_steps: int = 8,
        environment: JsonMapping | None = None,
        secret_provider: SecretProvider | None = None,
        secret_resolution: SecretResolutionReport | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if max_recovery_steps < 1:
            raise ValueError("max_recovery_steps must be positive")
        self._model = model
        self._state_store = state_store
        self._components = components
        self._event_sink = event_sink
        self._state_event_committer = (
            cast(StateEventCommitter, state_store)
            if hasattr(state_store, "commit_session_event")
            else None
        )
        self._context_compiler = context_compiler or BasicContextCompiler()
        self._observation_processor = ObservationProcessor(components)
        self._max_iterations = max_iterations
        self._max_recovery_steps = max_recovery_steps
        self._environment = immutable_json(environment)
        self._actions = ActionExecutor(
            components,
            self._environment,
            secret_provider=secret_provider,
            secret_resolution=secret_resolution,
        )

    async def run(self, goal: Goal, task: Task) -> ExecutionResult:
        state = AgentState(session_id=new_session_id(), goal=goal, current_task=task)
        state.tasks.append(task)
        session = start_session(state, self._components)
        await self._state_store.create_session(session.snapshot())
        await self._emit(
            state,
            "DomainActivated",
            data={
                "domain": self._components.active_domain.manifest.metadata.name,
                "domains": tuple(
                    f"{identity.name}@{identity.version}"
                    for identity in self._components.domain_composition.identities
                ),
            },
        )
        await self._emit(state, "GoalCreated")
        await self._emit(state, "TaskCreated")
        goal.status = GoalStatus.RUNNING
        task.status = TaskStatus.RUNNING
        await self._commit_session_event(
            session,
            self._runtime_event(state, "StateUpdated"),
        )
        return await self._loop(session)

    async def resume(
        self,
        session_id: SessionId,
        *,
        confirmed: bool | None = None,
    ) -> ExecutionResult:
        snapshot = await self._state_store.load_session(session_id)
        try:
            session = hydrate_session(snapshot, self._components)
        except SessionHydrationError as exc:
            return await self._reject_session(snapshot, str(exc))
        state = session.state
        pending = state.pending_action
        if state.goal.status is not GoalStatus.WAITING:
            return await self._settle(
                session,
                fail(session, ErrorCode.INVALID_STATE, "session is not waiting"),
            )
        if pending is not None and confirmed is None:
            return await self._settle(
                session,
                fail(
                    session,
                    ErrorCode.INVALID_STATE,
                    "resume requires confirmation for pending action",
                ),
            )
        if pending is not None and not confirmed:
            return await self._settle(
                session,
                fail(session, ErrorCode.CONFIRMATION_REJECTED, "user rejected pending action"),
            )
        state.goal.status = GoalStatus.RUNNING
        mark_current_task(session, TaskStatus.RUNNING)
        state.termination_reason = None
        await self._commit_session_event(
            session,
            self._runtime_event(state, "SessionResumed"),
        )
        if pending is not None:
            result = await self._drive(session, pending=pending)
            if result is not None:
                return result
        return await self._loop(session)

    async def pause(
        self,
        session_id: SessionId,
        *,
        reason: str = "session paused",
    ) -> ExecutionResult:
        snapshot = await self._state_store.load_session(session_id)
        try:
            session = hydrate_session(snapshot, self._components)
        except SessionHydrationError as exc:
            return await self._reject_session(snapshot, str(exc))
        state = session.state
        if state.goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return build_result(
                state,
                ExecutionStatus.FAILED,
                "cannot pause a terminal session",
                error_code=ErrorCode.INVALID_STATE,
            )
        return await self._settle(
            session,
            pause_transition(session, reason, event_type="SessionPaused"),
        )

    async def cancel(
        self,
        session_id: SessionId,
        *,
        reason: str = "session cancelled",
    ) -> ExecutionResult:
        snapshot = await self._state_store.load_session(session_id)
        try:
            session = hydrate_session(snapshot, self._components)
        except SessionHydrationError as exc:
            return await self._reject_session(snapshot, str(exc))
        state = session.state
        if state.goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return build_result(
                state,
                ExecutionStatus.FAILED,
                "cannot cancel a terminal session",
                error_code=ErrorCode.INVALID_STATE,
            )
        return await self._settle(session, cancel_transition(session, reason))

    async def _loop(self, session: SessionRuntimeState) -> ExecutionResult:
        state = session.state
        while state.iteration < self._max_iterations:
            state.iteration += 1
            await self._save(session)
            capabilities, input_contracts = self._capability_context()
            context = self._context_compiler.compile(
                state,
                capabilities,
                self._components.policy_engine.summary,
                self._components.context_providers,
                session.world(),
                session.query(limit=8),
                session.tasks,
                self._recall(session),
                input_contracts,
            )
            try:
                decision = await self._model.decide(context)
            except Exception as exc:
                return await self._settle(
                    session,
                    fail(session, ErrorCode.MODEL_FAILURE, f"model failed: {exc}"),
                )
            usage = model_usage(self._model)
            await self._emit(
                state,
                "DecisionGenerated",
                data={"decision_type": decision.type.value, "reason": decision.reason},
            )
            if usage is not None:
                await self._emit(
                    state,
                    "ModelUsageRecorded",
                    data={
                        "provider": usage.provider,
                        "model": usage.model,
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                        "estimated_cost_micros": usage.estimated_cost_micros,
                        "currency": usage.currency,
                    },
                )
            try:
                decision.validate()
            except ValueError as exc:
                return await self._settle(
                    session,
                    fail(session, ErrorCode.VALIDATION_ERROR, f"invalid decision: {exc}"),
                )
            context_error = self._validate_decision_context(
                decision,
                capabilities,
                input_contracts,
            )
            if context_error is not None:
                error_code, reason = context_error
                return await self._settle(session, fail(session, error_code, reason))
            result = await self._apply_decision(session, decision)
            if result is not None:
                return result
        return await self._settle(
            session,
            fail(
                session,
                ErrorCode.ITERATION_LIMIT,
                f"maximum iterations reached: {self._max_iterations}",
            ),
        )

    async def _apply_decision(
        self,
        session: SessionRuntimeState,
        decision: Decision,
    ) -> ExecutionResult | None:
        if decision.type is DecisionType.EXECUTE:
            return await self._drive(session, decision=decision)
        if decision.type is DecisionType.FINISH:
            return await self._settle(session, finish(session))
        if decision.type is DecisionType.WAIT:
            return await self._settle(
                session,
                pause_transition(session, "runtime paused by wait decision"),
            )
        if decision.type is DecisionType.ASK_USER:
            return await self._settle(
                session,
                pause_transition(
                    session,
                    "runtime paused for user input",
                    user_message=decision.message,
                ),
            )
        return await self._settle(
            session,
            fail(
                session,
                ErrorCode.VALIDATION_ERROR,
                f"unsupported decision type: {decision.type}",
            ),
        )

    async def _drive(
        self,
        session: SessionRuntimeState,
        *,
        decision: Decision | None = None,
        pending: PendingAction | None = None,
    ) -> ExecutionResult | None:
        """Run one action, then any recovery action it triggers.

        Recovery is a bounded loop, not recursion: a misconfigured domain can
        exhaust the step budget but can never grow the Python stack.
        """
        emit = self._emitter(session)
        for _ in range(self._max_recovery_steps):
            if pending is not None:
                outcome = await self._actions.execute(session, pending, emit, confirmed=True)
                pending = None
            else:
                assert decision is not None
                outcome = await self._actions.prepare(session, decision, emit)
            if isinstance(outcome, ActionRejected):
                return await self._settle(
                    session,
                    fail(session, outcome.error_code, outcome.reason),
                )
            if isinstance(outcome, ConfirmationRequired):
                target = outcome.pending.target or "target"
                return await self._settle(
                    session,
                    pause_transition(
                        session,
                        outcome.reason,
                        user_message=f"Confirm capability {outcome.pending.capability} on {target}",
                        event_type="ConfirmationRequired",
                        action_id=outcome.pending.action_id,
                    ),
                )
            step = await self._observe(session, outcome)
            if isinstance(step, Decision):
                decision = step
                continue
            return step
        return await self._settle(
            session,
            fail(
                session,
                ErrorCode.ITERATION_LIMIT,
                f"maximum recovery steps reached: {self._max_recovery_steps}",
            ),
        )

    async def _observe(
        self,
        session: SessionRuntimeState,
        outcome: ActionObserved,
    ) -> ExecutionResult | Decision | None:
        """Return a result to stop, a Decision to keep recovering, or None to continue."""
        state = session.state
        observation = outcome.observation
        if observation.status is not ObservationStatus.SUCCEEDED:
            return await self._plan_recovery(session, outcome)
        try:
            processed = self._observation_processor.process(
                session,
                observation,
                action=outcome.pending,
            )
        except ObservationRoutingError as exc:
            return await self._settle(
                session,
                fail(session, ErrorCode.EVALUATION_FAILED, str(exc)),
            )
        for evidence in processed.evidence:
            await self._emit(
                state,
                "EvidenceRecorded",
                action_id=observation.action_id,
                data={"evidence_id": evidence.id, "claim": evidence.claim},
            )
        if processed.evidence:
            await self._emit(
                state,
                "WorldModelUpdated",
                action_id=observation.action_id,
                data={"evidence_count": len(processed.evidence)},
            )
        for task in processed.created_tasks:
            await self._emit(
                state,
                "TaskCreated",
                data={"created_task_id": task.id, "description": task.description},
            )
        evaluation = processed.evaluation
        if evaluation is None:
            return await self._settle(
                session,
                fail(
                    session,
                    ErrorCode.EVALUATION_FAILED,
                    "observation processing did not produce an evaluation",
                ),
            )
        await self._emit(
            state,
            "EvaluationCompleted",
            action_id=observation.action_id,
            data={"status": evaluation.status.value, "evaluator": evaluation.evaluator_name},
        )
        if evaluation.status is EvaluationStatus.FAILED:
            return await self._settle(
                session,
                fail(session, ErrorCode.EVALUATION_FAILED, evaluation.reason),
            )
        if processed.next_task is not None:
            await self._emit(
                state,
                "TaskStarted",
                data={"started_task_id": processed.next_task.id},
            )
        await self._commit_session_event(
            session,
            self._runtime_event(
                state,
                "StateUpdated",
                action_id=observation.action_id,
                data={"task_status": state.current_task.status.value},
            ),
        )
        return None

    async def _plan_recovery(
        self,
        session: SessionRuntimeState,
        outcome: ActionObserved,
    ) -> ExecutionResult | Decision:
        state = session.state
        pending = outcome.pending
        observation = outcome.observation
        error_code = observation.error_code or ErrorCode.TOOL_FAILURE
        failure = Failure(
            state.current_task.id,
            error_code,
            classify_failure(error_code),
            observation.error or "tool execution failed",
            pending.capability,
            pending.arguments,
            pending.target,
        )
        recovery, key = self._components.recovery_manager.decide(
            failure,
            state.recovery_attempts,
        )
        if key:
            state.recovery_attempts[key] = recovery.attempt
        # Persist the spent budget before retrying so a crash cannot reset it.
        await self._commit_session_event(
            session,
            self._runtime_event(
                state,
                "RecoveryExhausted" if recovery.exhausted else "RecoveryPlanned",
                action_id=pending.action_id,
                data={"strategy": recovery.strategy.value, "rule": recovery.rule_name},
            ),
        )
        if recovery.strategy in {
            RecoveryStrategy.RETRY_ACTION,
            RecoveryStrategy.REOBSERVE,
            RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        }:
            return Decision(
                DecisionType.EXECUTE,
                f"recovery via {recovery.strategy.value}",
                capability=recovery.capability or pending.capability,
                target=pending.target,
                arguments=pending.arguments,
                expected_observations=("recovery",),
            )
        if recovery.strategy is RecoveryStrategy.ASK_USER:
            return await self._settle(
                session,
                pause_transition(
                    session,
                    failure.reason,
                    user_message=f"Recovery requires user input: {failure.reason}",
                ),
            )
        return await self._settle(session, fail(session, error_code, failure.reason))

    async def _settle(
        self,
        session: SessionRuntimeState,
        transition: Transition,
    ) -> ExecutionResult:
        if (
            transition.result.status
            in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
            and session.state.pending_action is not None
        ):
            pending = session.state.pending_action
            session.state.pending_action = None
            await self._actions.release_pending_resource(
                session,
                pending,
                self._emitter(session),
            )
        self._record_episodic(session, transition)
        event = self._runtime_event(
            session.state,
            transition.event_type,
            action_id=transition.action_id,
            data=transition.event_data,
        )
        await self._commit_session_event(session, event)
        return transition.result

    def _recall(self, session: SessionRuntimeState) -> tuple[MemoryRecord, ...]:
        """Run the three-stage pipeline: retrieve, filter.

        Memory enters the context only through this path, so it stays advisory
        and never reaches evidence, the world model, or the evaluator.
        """
        state = session.state
        request = RetrievalRequest(
            goal_description=state.goal.description,
            task_description=state.current_task.description,
            subjects=tuple(fact.subject for fact in session.world().facts),
            scope=self._components.memory_scope,
        )
        candidates = self._components.memory_retriever.retrieve(request)
        return self._components.memory_filter.filter(candidates, request)

    def _capability_context(
        self,
    ) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
        executable: list[CapabilityDefinition] = []
        contracts: list[CapabilityInputContract] = []
        for capability in self._components.capabilities.all():
            try:
                resolution = self._components.resolver.resolve_registration(capability.name)
            except (UnknownCapabilityError, CapabilityUnavailableError):
                continue
            tool = resolution.tool.definition
            executable.append(capability)
            contracts.append(
                CapabilityInputContract(
                    capability.name,
                    required_arguments=tool.required_arguments,
                    argument_schema=tool.argument_schema,
                )
            )
        return tuple(executable), tuple(contracts)

    def _validate_decision_context(
        self,
        decision: Decision,
        capabilities: tuple[CapabilityDefinition, ...],
        input_contracts: tuple[CapabilityInputContract, ...],
    ) -> tuple[ErrorCode, str] | None:
        if decision.type is not DecisionType.EXECUTE:
            return None
        capability = decision.capability or ""
        if capability in {item.name for item in capabilities}:
            contracts = {item.capability: item for item in input_contracts}
            contract = contracts.get(capability)
            if contract is None:
                return None
            argument_error = validate_argument_contract(
                required_arguments=contract.required_arguments,
                argument_schema=contract.argument_schema,
                arguments=decision.arguments,
            )
            if argument_error is not None:
                return (
                    ErrorCode.VALIDATION_ERROR,
                    f"invalid decision arguments for capability {capability}: {argument_error}",
                )
            return None
        try:
            self._components.capabilities.resolve_registration(capability)
        except UnknownCapabilityError as exc:
            return ErrorCode.UNKNOWN_CAPABILITY, str(exc)
        return (
            ErrorCode.NO_CAPABILITY_TOOL,
            f"capability is not executable in current context: {capability}",
        )

    def _record_episodic(
        self,
        session: SessionRuntimeState,
        transition: Transition,
    ) -> None:
        """Write a single episodic record at a terminal transition.

        WAITING is not a terminal state: the session may resume, so there is no
        settled experience to record yet. Only COMPLETED / FAILED produce an
        episodic memory, which future sessions of the same runtime may recall.
        """
        result = transition.result
        if result.status is ExecutionStatus.WAITING:
            return
        state = session.state
        kind = MemoryKind.EPISODIC
        content = f"Goal '{state.goal.description}' ended as {result.status.value}: {result.reason}"
        record = MemoryRecord(
            kind=kind,
            subject=f"session {state.session_id}",
            content=content,
            scope=self._components.memory_scope or "",
            confidence=1.0,
            source_session_id=state.session_id,
        )
        self._components.memory_store.add(record)

    async def _reject_session(self, snapshot: SessionSnapshot, reason: str) -> ExecutionResult:
        """Fail a session that could not be hydrated into a runtime state."""
        state = snapshot.state
        state.goal.status = GoalStatus.FAILED
        state.current_task.status = TaskStatus.FAILED
        state.tasks = [state.current_task]
        state.termination_reason = reason
        state.error_code = ErrorCode.INVALID_STATE
        failed_snapshot = session_from_state(
            state,
            domain_name=snapshot.domain_name,
            domain_version=snapshot.domain_version,
            domain_identities=snapshot.domains,
        )
        failed_snapshot.version = snapshot.version
        event = self._runtime_event(
            state,
            "GoalFailed",
            data={"error_code": ErrorCode.INVALID_STATE.value, "reason": reason},
        )
        if self._state_event_committer is None:
            await self._state_store.save_session(failed_snapshot)
            await self._event_sink.emit(event)
        else:
            await self._state_event_committer.commit_session_event(failed_snapshot, event)
        return build_result(
            state,
            ExecutionStatus.FAILED,
            reason,
            error_code=ErrorCode.INVALID_STATE,
        )

    async def _save(self, session: SessionRuntimeState) -> None:
        snapshot = session.snapshot()
        await self._state_store.save_session(snapshot)
        session.version = snapshot.version

    async def _commit_session_event(
        self,
        session: SessionRuntimeState,
        event: RuntimeEvent,
    ) -> None:
        snapshot = session.snapshot()
        if self._state_event_committer is None:
            await self._state_store.save_session(snapshot)
            session.version = snapshot.version
            await self._event_sink.emit(event)
            return
        await self._state_event_committer.commit_session_event(snapshot, event)
        session.version = snapshot.version

    def _emitter(
        self,
        session: SessionRuntimeState,
    ) -> Callable[[str, ActionId | None, dict[str, object]], Awaitable[None]]:
        """Narrow the event sink down to what action execution is allowed to do."""

        async def emit(
            event_type: str,
            action_id: ActionId | None,
            data: dict[str, object],
        ) -> None:
            await self._emit(session.state, event_type, action_id=action_id, data=data)

        return emit

    async def _emit(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        await self._event_sink.emit(
            self._runtime_event(state, event_type, action_id=action_id, data=data)
        )

    def _runtime_event(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            type=event_type,
            session_id=state.session_id,
            goal_id=state.goal.id,
            task_id=state.current_task.id,
            action_id=action_id,
            data=data or {},
        )
