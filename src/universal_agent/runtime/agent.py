from __future__ import annotations

from universal_agent.capability import (
    CapabilityUnavailableError,
    UnknownCapabilityError,
)
from universal_agent.context import BasicContextCompiler, ContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    Decision,
    DecisionType,
    ErrorCode,
    EvaluationStatus,
    ExecutionResult,
    ExecutionStatus,
    Goal,
    GoalStatus,
    Observation,
    ObservationStatus,
    PendingAction,
    PolicyContext,
    PolicyEffect,
    RuntimeEvent,
    Task,
    TaskStatus,
    ToolCall,
    immutable_json,
    new_action_id,
    new_session_id,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.evidence import EvidenceQuery
from universal_agent.model import ModelAdapter
from universal_agent.observation import ObservationFactory
from universal_agent.recovery import Failure, RecoveryStrategy, classify_failure
from universal_agent.runtime.events import EventSink
from universal_agent.runtime.processing import ObservationProcessor
from universal_agent.state import StateStore
from universal_agent.tasks import TaskManager
from universal_agent.tools import ToolRuntime


class AgentRuntime:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        state_store: StateStore,
        components: RuntimeComponents,
        event_sink: EventSink,
        context_compiler: ContextCompiler | None = None,
        max_iterations: int = 20,
        environment: dict[str, object] | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self._model = model
        self._state_store = state_store
        self._components = components
        self._tool_runtime = ToolRuntime(components.tools)
        self._event_sink = event_sink
        self._context_compiler = context_compiler or BasicContextCompiler()
        self._observation_factory = ObservationFactory()
        self._observation_processor = ObservationProcessor(components)
        self._task_managers: dict[str, TaskManager] = {}
        self._max_iterations = max_iterations
        self._environment = immutable_json(environment)

    async def run(self, goal: Goal, task: Task) -> ExecutionResult:
        state = AgentState(session_id=new_session_id(), goal=goal, current_task=task)
        state.tasks.append(task)
        self._task_managers[str(state.session_id)] = TaskManager(task)
        await self._state_store.create(state)
        await self._emit(
            state,
            "DomainActivated",
            data={"domain": self._components.active_domain.manifest.metadata.name},
        )
        await self._emit(state, "GoalCreated")
        await self._emit(state, "TaskCreated")
        goal.status = GoalStatus.RUNNING
        task.status = TaskStatus.RUNNING
        await self._state_store.save(state)
        await self._emit(state, "StateUpdated")
        return await self._loop(state)

    async def resume(self, session_id: str, *, confirmed: bool) -> ExecutionResult:
        state = await self._state_store.load(session_id)
        pending = state.pending_action
        if pending is None or state.goal.status is not GoalStatus.WAITING:
            return await self._fail(state, ErrorCode.INVALID_STATE, "session has no pending action")
        if not confirmed:
            state.pending_action = None
            return await self._fail(
                state,
                ErrorCode.CONFIRMATION_REJECTED,
                "user rejected pending action",
            )
        state.goal.status = GoalStatus.RUNNING
        state.current_task.status = TaskStatus.RUNNING
        execution = await self._execute_pending(state, pending, confirmed=True)
        if execution is not None:
            return execution
        return await self._loop(state)

    async def _loop(self, state: AgentState) -> ExecutionResult:
        while state.iteration < self._max_iterations:
            state.iteration += 1
            await self._state_store.save(state)
            context = self._context_compiler.compile(
                state,
                self._components.capabilities.all(),
                self._components.policy_engine.summary,
                self._components.active_domain.context_providers,
                self._components.world_model.snapshot(state.session_id),
                self._components.evidence_store.query(
                    EvidenceQuery(state.session_id, task_id=state.current_task.id, limit=8)
                ),
                self._task_manager(state),
            )
            try:
                decision = await self._model.decide(context)
            except Exception as exc:
                return await self._fail(state, ErrorCode.MODEL_FAILURE, f"model failed: {exc}")
            await self._emit(
                state,
                "DecisionGenerated",
                data={"decision_type": decision.type.value, "reason": decision.reason},
            )
            try:
                decision.validate()
            except ValueError as exc:
                return await self._fail(
                    state,
                    ErrorCode.VALIDATION_ERROR,
                    f"invalid decision: {exc}",
                )
            result = await self._apply_decision(state, decision)
            if result is not None:
                return result
        return await self._fail(
            state,
            ErrorCode.ITERATION_LIMIT,
            f"maximum iterations reached: {self._max_iterations}",
        )

    async def _apply_decision(
        self,
        state: AgentState,
        decision: Decision,
    ) -> ExecutionResult | None:
        if decision.type is DecisionType.EXECUTE:
            return await self._prepare_action(state, decision)
        if decision.type is DecisionType.FINISH:
            return await self._finish(state)
        if decision.type is DecisionType.WAIT:
            return await self._pause(state, "runtime paused by wait decision")
        if decision.type is DecisionType.ASK_USER:
            return await self._pause(
                state,
                "runtime paused for user input",
                user_message=decision.message,
            )
        return await self._fail(
            state,
            ErrorCode.VALIDATION_ERROR,
            f"unsupported decision type: {decision.type}",
        )

    async def _prepare_action(
        self,
        state: AgentState,
        decision: Decision,
    ) -> ExecutionResult | None:
        try:
            capability, tool = self._components.resolver.resolve(decision.capability or "")
        except UnknownCapabilityError as exc:
            return await self._fail(state, ErrorCode.UNKNOWN_CAPABILITY, str(exc))
        except CapabilityUnavailableError as exc:
            return await self._fail(state, ErrorCode.NO_CAPABILITY_TOOL, str(exc))
        pending = PendingAction(
            action_id=new_action_id(),
            capability=capability.name,
            tool_name=tool.definition.name,
            target=decision.target,
            arguments=decision.arguments,
        )
        await self._emit(
            state,
            "CapabilityResolved",
            action_id=pending.action_id,
            data={"capability": capability.name, "tool_name": tool.definition.name},
        )
        return await self._execute_pending(state, pending, confirmed=False)

    async def _execute_pending(
        self,
        state: AgentState,
        pending: PendingAction,
        *,
        confirmed: bool,
    ) -> ExecutionResult | None:
        capability, tool = self._components.resolver.resolve(pending.capability)
        if tool.definition.name != pending.tool_name:
            return await self._fail(
                state,
                ErrorCode.INVALID_STATE,
                "pending action tool resolution changed",
            )
        policy_result = self._components.policy_engine.check(
            PolicyContext(
                session_id=state.session_id,
                goal_id=state.goal.id,
                task_id=state.current_task.id,
                action_id=pending.action_id,
                capability=capability,
                tool=tool.definition,
                target=pending.target,
                arguments=pending.arguments,
                environment=self._environment,
                confirmed=confirmed,
            )
        )
        await self._emit(
            state,
            "PolicyChecked",
            action_id=pending.action_id,
            data={"effect": policy_result.effect.value, "policy": policy_result.policy_name},
        )
        if policy_result.effect is PolicyEffect.DENY:
            return await self._fail(state, ErrorCode.POLICY_DENIED, policy_result.reason)
        if policy_result.effect is PolicyEffect.REQUIRE_CONFIRMATION:
            state.pending_action = pending
            return await self._pause(
                state,
                policy_result.reason,
                user_message=(
                    f"Confirm capability {pending.capability} on {pending.target or 'target'}"
                ),
                event_type="ConfirmationRequired",
                action_id=pending.action_id,
            )
        state.pending_action = None
        return await self._execute_tool(state, pending)

    async def _execute_tool(
        self,
        state: AgentState,
        pending: PendingAction,
    ) -> ExecutionResult | None:
        call = ToolCall(
            action_id=pending.action_id,
            tool_name=pending.tool_name,
            capability=pending.capability,
            arguments=pending.arguments,
            target=pending.target,
        )
        await self._emit(
            state,
            "ActionStarted",
            action_id=call.action_id,
            data={"tool_name": call.tool_name, "capability": call.capability},
        )
        tool_result = await self._tool_runtime.execute(call)
        await self._emit(
            state,
            "ActionCompleted",
            action_id=call.action_id,
            data={"status": tool_result.status.value},
        )
        observation = self._observation_factory.from_tool_result(
            task_id=state.current_task.id,
            call=call,
            result=tool_result,
        )
        state.observations.append(observation)
        await self._emit(
            state,
            "ObservationReceived",
            action_id=observation.action_id,
            data={"observation_id": observation.id, "status": observation.status.value},
        )
        if observation.status is not ObservationStatus.SUCCEEDED:
            return await self._recover(state, pending, observation)
        processed = self._observation_processor.process(
            state,
            observation,
            self._task_manager(state),
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
            state.tasks.append(task)
            await self._emit(
                state,
                "TaskCreated",
                data={"created_task_id": task.id, "description": task.description},
            )
        evaluation = processed.evaluation
        if evaluation is None:
            return await self._fail(
                state,
                ErrorCode.EVALUATION_FAILED,
                "observation processing did not produce an evaluation",
            )
        await self._emit(
            state,
            "EvaluationCompleted",
            action_id=observation.action_id,
            data={"status": evaluation.status.value, "evaluator": evaluation.evaluator_name},
        )
        if evaluation.status is EvaluationStatus.FAILED:
            return await self._fail(state, ErrorCode.EVALUATION_FAILED, evaluation.reason)
        if processed.next_task is not None:
            await self._emit(
                state,
                "TaskStarted",
                data={"started_task_id": processed.next_task.id},
            )
        await self._state_store.save(state)
        await self._emit(
            state,
            "StateUpdated",
            action_id=observation.action_id,
            data={"task_status": state.current_task.status.value},
        )
        return None

    async def _recover(
        self,
        state: AgentState,
        pending: PendingAction,
        observation: Observation,
    ) -> ExecutionResult | None:
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
        await self._state_store.save(state)
        await self._emit(
            state,
            "RecoveryExhausted" if recovery.exhausted else "RecoveryPlanned",
            action_id=pending.action_id,
            data={"strategy": recovery.strategy.value, "rule": recovery.rule_name},
        )
        if recovery.strategy in {
            RecoveryStrategy.RETRY_ACTION,
            RecoveryStrategy.REOBSERVE,
            RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        }:
            capability = recovery.capability or pending.capability
            decision = Decision(
                DecisionType.EXECUTE,
                f"recovery via {recovery.strategy.value}",
                capability=capability,
                target=pending.target,
                arguments=pending.arguments,
                expected_observations=("recovery",),
            )
            return await self._prepare_action(state, decision)
        if recovery.strategy is RecoveryStrategy.ASK_USER:
            return await self._pause(
                state,
                failure.reason,
                user_message=f"Recovery requires user input: {failure.reason}",
            )
        return await self._fail(state, error_code, failure.reason)

    def _task_manager(self, state: AgentState) -> TaskManager:
        key = str(state.session_id)
        manager = self._task_managers.get(key)
        if manager is None:
            manager = TaskManager(state.current_task)
            self._task_managers[key] = manager
        return manager

    async def _finish(self, state: AgentState) -> ExecutionResult:
        evaluation = state.latest_evaluation
        if (
            self._task_manager(state).has_unfinished()
            or state.current_task.status is not TaskStatus.COMPLETED
            or evaluation is None
            or evaluation.status is not EvaluationStatus.COMPLETED
        ):
            return await self._fail(
                state,
                ErrorCode.INVALID_STATE,
                "finish rejected because evaluator has not completed the task and goal",
            )
        state.goal.status = GoalStatus.COMPLETED
        state.termination_reason = evaluation.reason
        await self._state_store.save(state)
        await self._emit(state, "GoalCompleted")
        return self._result(state, ExecutionStatus.COMPLETED, evaluation.reason)

    async def _pause(
        self,
        state: AgentState,
        reason: str,
        *,
        user_message: str | None = None,
        event_type: str = "GoalWaiting",
        action_id: ActionId | None = None,
    ) -> ExecutionResult:
        state.goal.status = GoalStatus.WAITING
        state.current_task.status = TaskStatus.WAITING
        state.termination_reason = reason
        await self._state_store.save(state)
        await self._emit(state, event_type, action_id=action_id)
        return self._result(
            state,
            ExecutionStatus.WAITING,
            reason,
            user_message=user_message,
        )

    async def _fail(
        self,
        state: AgentState,
        error_code: ErrorCode,
        reason: str,
    ) -> ExecutionResult:
        state.goal.status = GoalStatus.FAILED
        state.current_task.status = TaskStatus.FAILED
        state.termination_reason = reason
        state.error_code = error_code
        await self._state_store.save(state)
        await self._emit(
            state,
            "GoalFailed",
            data={"error_code": error_code.value, "reason": reason},
        )
        return self._result(state, ExecutionStatus.FAILED, reason, error_code=error_code)

    def _result(
        self,
        state: AgentState,
        status: ExecutionStatus,
        reason: str,
        *,
        error_code: ErrorCode | None = None,
        user_message: str | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            status=status,
            session_id=state.session_id,
            goal_id=state.goal.id,
            task_id=state.current_task.id,
            iterations=state.iteration,
            reason=reason,
            error_code=error_code,
            user_message=user_message,
        )

    async def _emit(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        await self._event_sink.emit(
            RuntimeEvent(
                type=event_type,
                session_id=state.session_id,
                goal_id=state.goal.id,
                task_id=state.current_task.id,
                action_id=action_id,
                data=data or {},
            )
        )
