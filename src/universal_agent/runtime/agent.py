from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, cast

from universal_agent.context import BasicContextCompiler, ContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    CapabilityInputContract,
    Decision,
    DecisionContext,
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
    SessionId,
    Task,
    TaskStatus,
    immutable_json,
    new_session_id,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.goals import DefaultGoalCompiler, GoalCompilation, GoalCompiler
from universal_agent.model import ModelAdapter, model_usage
from universal_agent.model.router import ModelRouter, ModelSelectionContext
from universal_agent.recovery import Failure, RecoveryStrategy, classify_failure
from universal_agent.runtime.actions import (
    ActionExecutor,
    ActionObserved,
    ActionRejected,
    ConfirmationRequired,
)
from universal_agent.runtime.capabilities import CapabilityAdvisor
from universal_agent.runtime.decision import DecisionEngine, normalize_runtime_decision
from universal_agent.runtime.emission import EventEmitter
from universal_agent.runtime.events import EventSink
from universal_agent.runtime.initial_state import seed_initial_state
from universal_agent.runtime.memory import MemoryConsultant
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
from universal_agent.security.sandbox import Sandbox
from universal_agent.state import (
    SessionSnapshot,
    SessionStore,
)
from universal_agent.state.event_store import EventStore
from universal_agent.tasks import TaskManager

_RISK_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


@dataclass(slots=True)
class _SessionControl:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    pause_requested: bool = False
    pause_reason: str | None = None


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
        max_total_cost_micros: int | None = None,
        max_total_tokens: int | None = None,
        environment: JsonMapping | None = None,
        secret_provider: SecretProvider | None = None,
        secret_resolution: SecretResolutionReport | None = None,
        event_store: EventStore | None = None,
        goal_compiler: GoalCompiler | None = None,
        decision_engine: DecisionEngine | None = None,
        model_router: ModelRouter | None = None,
        sandbox: Sandbox | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if max_recovery_steps < 1:
            raise ValueError("max_recovery_steps must be positive")
        self._model = model
        self._state_store = state_store
        self._components = components
        self._event_store = event_store
        if self._event_store is None and hasattr(event_sink, "append"):
            self._event_store = cast(EventStore, event_sink)
        self._events = EventEmitter(
            event_sink=event_sink,
            state_store=state_store,
            event_store=self._event_store,
        )
        self._context_compiler = context_compiler or BasicContextCompiler()
        self._capability_advisor = CapabilityAdvisor(components)
        self._memory = MemoryConsultant(components)
        self._observation_processor = ObservationProcessor(components)
        self._max_iterations = max_iterations
        self._max_recovery_steps = max_recovery_steps
        self._max_total_cost_micros = max_total_cost_micros
        self._max_total_tokens = max_total_tokens
        self._environment = immutable_json(environment)
        self._goal_compiler = goal_compiler
        self._decision_engine = decision_engine
        self._model_router = model_router
        self._actions = ActionExecutor(
            components,
            self._environment,
            secret_provider=secret_provider,
            secret_resolution=secret_resolution,
            sandbox=sandbox,
        )
        self._capability_context_cache: (
            tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]] | None
        ) = None
        self._session_controls: dict[SessionId, _SessionControl] = {}

    async def run(
        self,
        goal: Goal,
        task: Task,
        *,
        initial_state: JsonMapping | None = None,
        read_only: bool = False,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        if self._goal_compiler is not None:
            compilation = await self._goal_compiler.compile(goal)
            return await self._run_compilation(
                goal,
                compilation,
                initial_state=initial_state,
                read_only=read_only,
                timeout_seconds=timeout_seconds,
            )
        return await self._start_run(
            goal,
            task,
            initial_state=initial_state,
            read_only=read_only,
            timeout_seconds=timeout_seconds,
        )

    async def run_compiled(
        self,
        goal: Goal,
        *,
        initial_state: JsonMapping | None = None,
        read_only: bool = False,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        compilation = await DefaultGoalCompiler().compile(goal)
        return await self._run_compilation(
            goal,
            compilation,
            initial_state=initial_state,
            read_only=read_only,
            timeout_seconds=timeout_seconds,
        )

    async def _run_compilation(
        self,
        goal: Goal,
        compilation: GoalCompilation,
        *,
        initial_state: JsonMapping | None,
        read_only: bool,
        timeout_seconds: float | None,
    ) -> ExecutionResult:
        tasks = TaskManager.from_specs(compilation.initial_tasks)
        return await self._start_run(
            goal,
            tasks.current,
            initial_state=initial_state,
            read_only=read_only,
            tasks=tasks,
            compilation=compilation,
            timeout_seconds=timeout_seconds,
        )

    async def _start_run(
        self,
        goal: Goal,
        task: Task,
        *,
        initial_state: JsonMapping | None = None,
        read_only: bool = False,
        tasks: TaskManager | None = None,
        compilation: GoalCompilation | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        state = AgentState(
            session_id=new_session_id(),
            goal=goal,
            current_task=task,
            read_only=read_only,
        )
        state.tasks = list(tasks.all()) if tasks is not None else [task]
        session = start_session(state, self._components, tasks=tasks)
        await self._state_store.create_session(session.snapshot())
        if initial_state:
            seed_initial_state(session, initial_state)
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
        if compilation is not None:
            await self._emit(
                state,
                "GoalCompiled",
                data={
                    "task_count": len(compilation.initial_tasks),
                    "notes": compilation.notes,
                },
            )
        await self._emit(state, "GoalCreated")
        for created_task in session.tasks.all():
            await self._emit(
                state,
                "TaskCreated",
                data={
                    "created_task_id": created_task.id,
                    "description": created_task.description,
                },
            )
        goal.status = GoalStatus.RUNNING
        mark_current_task(session, TaskStatus.RUNNING)
        await self._events.commit_session_event(
            session,
            self._events.runtime_event(state, "StateUpdated"),
        )
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        return await self._run_controlled(session, deadline=deadline)

    async def resume(
        self,
        session_id: SessionId,
        *,
        confirmed: bool | None = None,
    ) -> ExecutionResult:
        control = self._control_for(session_id)
        async with control.lock:
            snapshot = await self._load_session(session_id)
            try:
                session = hydrate_session(snapshot, self._components)
            except SessionHydrationError as exc:
                return await self._reject_session(snapshot, str(exc))
            state = session.state
            pending = state.pending_action
            if state.goal.status is not GoalStatus.WAITING:
                return build_result(
                    state,
                    ExecutionStatus.FAILED,
                    "session is not waiting",
                    error_code=ErrorCode.INVALID_STATE,
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
                state.goal.status = GoalStatus.RUNNING
                mark_current_task(session, TaskStatus.RUNNING)
                state.termination_reason = None
                state.pending_action = None
                await self._actions.release_pending_resource(
                    session,
                    pending,
                    self._events.emitter_for(session),
                )
                recovery_step = await self._plan_recovery_for_pending(
                    session,
                    pending,
                    Failure(
                        state.current_task.id,
                        ErrorCode.CONFIRMATION_REJECTED,
                        classify_failure(ErrorCode.CONFIRMATION_REJECTED),
                        "user rejected pending action",
                        pending.capability,
                        pending.arguments,
                        pending.target,
                    ),
                )
                if isinstance(recovery_step, Decision):
                    return await self._continue_controlled(session, control, decision=recovery_step)
                return recovery_step
            state.goal.status = GoalStatus.RUNNING
            mark_current_task(session, TaskStatus.RUNNING)
            state.termination_reason = None
            await self._events.commit_session_event(
                session,
                self._events.runtime_event(state, "SessionResumed"),
            )
            return await self._continue_controlled(session, control, pending=pending)

    async def pause(
        self,
        session_id: SessionId,
        *,
        reason: str = "session paused",
    ) -> ExecutionResult:
        control = self._session_controls.get(session_id)
        active = None if control is None else control.active_task
        current = asyncio.current_task()
        if (
            control is not None
            and active is not None
            and active is not current
            and not active.done()
        ):
            control.pause_requested = True
            control.pause_reason = reason
            return await asyncio.shield(active)
        snapshot = await self._load_session(session_id)
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
        control = self._session_controls.get(session_id)
        active = None if control is None else control.active_task
        current = asyncio.current_task()
        if (
            control is not None
            and active is not None
            and active is not current
            and not active.done()
        ):
            control.cancel_requested = True
            control.cancel_reason = reason
            active.cancel()
            return await asyncio.shield(active)
        snapshot = await self._load_session(session_id)
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

    async def _loop(
        self,
        session: SessionRuntimeState,
        *,
        deadline: float | None = None,
    ) -> ExecutionResult:
        state = session.state
        while state.iteration < self._max_iterations:
            budget_pause = await self._pause_if_budget_expired(session, deadline)
            if budget_pause is not None:
                return budget_pause
            requested = await self._requested_control_transition(session)
            if requested is not None:
                return requested
            state.iteration += 1
            await self._save(session)
            all_capabilities, all_input_contracts = self._get_capability_context()
            capabilities, input_contracts = _constrain_capability_context(
                state,
                all_capabilities,
                all_input_contracts,
            )
            context = self._context_compiler.compile(
                state,
                capabilities,
                self._components.policy_engine.summary,
                self._components.context_providers,
                session.world(),
                session.query(limit=8),
                session.tasks,
                self._memory.recall(session),
                input_contracts,
            )
            try:
                decision, usage_source = await self._decide(context)
            except Exception as exc:
                return await self._settle(
                    session,
                    fail(session, ErrorCode.MODEL_FAILURE, f"model failed: {exc}"),
                )
            usage = model_usage(usage_source)
            await self._emit(
                state,
                "DecisionGenerated",
                data=self._events.decision_event_data(decision),
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
                state.cumulative_cost_micros += usage.estimated_cost_micros
                state.cumulative_tokens += usage.total_tokens
                if (
                    self._max_total_cost_micros is not None
                    and state.cumulative_cost_micros >= self._max_total_cost_micros
                ):
                    limit = self._max_total_cost_micros
                    current = state.cumulative_cost_micros
                    return await self._settle(
                        session,
                        fail(
                            session,
                            ErrorCode.COST_LIMIT_EXCEEDED,
                            f"cost limit reached: {current} >= {limit}",
                        ),
                    )
                if (
                    self._max_total_tokens is not None
                    and state.cumulative_tokens >= self._max_total_tokens
                ):
                    limit = self._max_total_tokens
                    current = state.cumulative_tokens
                    return await self._settle(
                        session,
                        fail(
                            session,
                            ErrorCode.COST_LIMIT_EXCEEDED,
                            f"token limit reached: {current} >= {limit}",
                        ),
                    )
            budget_pause = await self._pause_if_budget_expired(session, deadline)
            if budget_pause is not None:
                return budget_pause
            decision = normalize_runtime_decision(decision)
            try:
                decision.validate()
            except ValueError as exc:
                reason = f"invalid decision: {exc}"
                await self._events.emit_decision_rejected(
                    state,
                    decision,
                    ErrorCode.VALIDATION_ERROR,
                    reason,
                    validation_stage="contract",
                )
                return await self._settle(
                    session,
                    fail(session, ErrorCode.VALIDATION_ERROR, reason),
                )
            constraint_error = _validate_session_constraints(state, decision, all_capabilities)
            if constraint_error is not None:
                error_code, reason = constraint_error
                await self._events.emit_decision_rejected(
                    state,
                    decision,
                    error_code,
                    reason,
                    validation_stage="session_constraints",
                )
                return await self._settle(session, fail(session, error_code, reason))
            context_error = self._capability_advisor.validate_decision_context(
                decision,
                capabilities,
                input_contracts,
            )
            if context_error is not None:
                error_code, reason = context_error
                await self._events.emit_decision_rejected(
                    state,
                    decision,
                    error_code,
                    reason,
                    validation_stage="context",
                )
                return await self._settle(session, fail(session, error_code, reason))
            await self._emit(
                state,
                "DecisionValidated",
                data={
                    **self._events.decision_event_data(decision),
                    "available_capability_count": len(capabilities),
                },
            )
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

    async def _pause_if_budget_expired(
        self,
        session: SessionRuntimeState,
        deadline: float | None,
    ) -> ExecutionResult | None:
        if deadline is None or time.monotonic() < deadline:
            return None
        return await self._settle(
            session,
            pause_transition(
                session,
                "wall-clock budget expired; session paused at runtime boundary",
                event_type="SessionPaused",
            ),
        )

    async def _decide(self, context: DecisionContext) -> tuple[Decision, ModelAdapter]:
        if self._decision_engine is not None:
            return await self._decision_engine.decide(context), self._model
        if self._model_router is not None:
            route = await self._model_router.select(self._model_selection_context(context))
            return await route.adapter.decide(context), route.adapter
        return await self._model.decide(context), self._model

    def _model_selection_context(self, context: DecisionContext) -> ModelSelectionContext:
        capabilities = context.capabilities
        if not capabilities:
            from universal_agent.core import RiskLevel

            return ModelSelectionContext(risk=RiskLevel.LOW, readonly=True)
        highest = max(
            capabilities,
            key=lambda item: _RISK_RANK.get(item.risk.value, 0),
        )
        readonly = all(item.category.value != "mutation" for item in capabilities)
        return ModelSelectionContext(
            risk=highest.risk,
            capability_category=highest.category,
            readonly=readonly,
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
        emit = self._events.emitter_for(session)
        for _ in range(self._max_recovery_steps):
            requested = await self._requested_control_transition(session)
            if requested is not None:
                return requested
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

    def _control_for(self, session_id: SessionId) -> _SessionControl:
        control = self._session_controls.get(session_id)
        if control is None:
            control = _SessionControl()
            self._session_controls[session_id] = control
        return control

    async def _run_controlled(
        self,
        session: SessionRuntimeState,
        *,
        deadline: float | None = None,
    ) -> ExecutionResult:
        control = self._control_for(session.state.session_id)
        async with control.lock:
            return await self._continue_controlled(session, control, deadline=deadline)

    async def _continue_controlled(
        self,
        session: SessionRuntimeState,
        control: _SessionControl,
        *,
        decision: Decision | None = None,
        pending: PendingAction | None = None,
        deadline: float | None = None,
    ) -> ExecutionResult:
        current = asyncio.current_task()
        control.active_task = current
        try:
            requested = await self._requested_control_transition(session)
            if requested is not None:
                return requested
            if pending is not None or decision is not None:
                result = await self._drive(session, decision=decision, pending=pending)
                if result is not None:
                    return result
            return await self._loop(session, deadline=deadline)
        except asyncio.CancelledError:
            if control.cancel_requested:
                return await self._settle(
                    session,
                    cancel_transition(session, control.cancel_reason or "session cancelled"),
                )
            raise
        finally:
            if control.active_task is current:
                control.active_task = None
            control.cancel_requested = False
            control.cancel_reason = None
            control.pause_requested = False
            control.pause_reason = None

    async def _requested_control_transition(
        self,
        session: SessionRuntimeState,
    ) -> ExecutionResult | None:
        control = self._session_controls.get(session.state.session_id)
        if control is None:
            return None
        if control.cancel_requested:
            return await self._settle(
                session,
                cancel_transition(session, control.cancel_reason or "session cancelled"),
            )
        if control.pause_requested:
            reason = control.pause_reason or "session paused"
            control.pause_requested = False
            control.pause_reason = None
            return await self._settle(
                session,
                pause_transition(session, reason, event_type="SessionPaused"),
            )
        return None

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
            return await self._plan_recovery_for_failure(
                session,
                outcome,
                Failure(
                    state.current_task.id,
                    ErrorCode.EVALUATION_FAILED,
                    classify_failure(ErrorCode.EVALUATION_FAILED),
                    evaluation.reason,
                    outcome.pending.capability,
                    outcome.pending.arguments,
                    outcome.pending.target,
                ),
            )
        if processed.next_task is not None:
            await self._emit(
                state,
                "TaskStarted",
                data={"started_task_id": processed.next_task.id},
            )
        await self._events.commit_session_event(
            session,
            self._events.runtime_event(
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
        return await self._plan_recovery_for_failure(
            session,
            outcome,
            Failure(
                state.current_task.id,
                error_code,
                classify_failure(error_code),
                observation.error or "tool execution failed",
                pending.capability,
                pending.arguments,
                pending.target,
            ),
        )

    async def _plan_recovery_for_failure(
        self,
        session: SessionRuntimeState,
        outcome: ActionObserved,
        failure: Failure,
    ) -> ExecutionResult | Decision:
        return await self._plan_recovery_for_pending(session, outcome.pending, failure)

    async def _plan_recovery_for_pending(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        failure: Failure,
    ) -> ExecutionResult | Decision:
        state = session.state
        recovery, key = self._components.recovery_manager.decide(
            failure,
            state.recovery_attempts,
        )
        if key:
            state.recovery_attempts[key] = recovery.attempt
        # Persist the spent budget before retrying so a crash cannot reset it.
        await self._events.commit_session_event(
            session,
            self._events.runtime_event(
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
        return await self._settle(session, fail(session, failure.error_code, failure.reason))

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
                self._events.emitter_for(session),
            )
        self._memory.record_episodic(session, transition)
        event = self._events.runtime_event(
            session.state,
            transition.event_type,
            action_id=transition.action_id,
            data=transition.event_data,
        )
        await self._events.commit_session_event(session, event)
        return transition.result

    def _get_capability_context(
        self,
    ) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
        if self._capability_context_cache is not None:
            return self._capability_context_cache
        self._capability_context_cache = self._capability_advisor.context()
        return self._capability_context_cache

    async def _reject_session(self, snapshot: SessionSnapshot, reason: str) -> ExecutionResult:
        return await self._events.reject_session(snapshot, reason)

    async def _save(self, session: SessionRuntimeState) -> None:
        snapshot = session.snapshot()
        await self._state_store.save_session(snapshot)
        session.version = snapshot.version

    async def _load_session(self, session_id: SessionId) -> SessionSnapshot:
        from universal_agent.state.event_store import EventReplayError
        from universal_agent.state.store import StateNotFoundError

        try:
            return await self._state_store.load_session(session_id)
        except StateNotFoundError:
            if self._event_store is not None:
                from universal_agent.state.event_store import rebuild_session_snapshot

                try:
                    return rebuild_session_snapshot(self._event_store, session_id)
                except EventReplayError as err:
                    raise StateNotFoundError(f"session not found: {session_id}") from err
            raise

    async def _emit(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        await self._events.emit(state, event_type, action_id=action_id, data=data)


def _constrain_capability_context(
    state: AgentState,
    capabilities: tuple[CapabilityDefinition, ...],
    input_contracts: tuple[CapabilityInputContract, ...],
) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
    if not state.read_only:
        return capabilities, input_contracts
    allowed_capability_names = {
        capability.name
        for capability in capabilities
        if capability.category is not CapabilityCategory.MUTATION
    }
    return (
        tuple(
            capability for capability in capabilities if capability.name in allowed_capability_names
        ),
        tuple(
            contract
            for contract in input_contracts
            if contract.capability in allowed_capability_names
        ),
    )


def _validate_session_constraints(
    state: AgentState,
    decision: Decision,
    capabilities: tuple[CapabilityDefinition, ...],
) -> tuple[ErrorCode, str] | None:
    if not state.read_only or decision.type is not DecisionType.EXECUTE:
        return None
    capability_name = decision.capability or ""
    capability = next((item for item in capabilities if item.name == capability_name), None)
    if capability is None or capability.category is not CapabilityCategory.MUTATION:
        return None
    return (
        ErrorCode.POLICY_DENIED,
        f"read-only session cannot execute mutation capability: {capability.name}",
    )
