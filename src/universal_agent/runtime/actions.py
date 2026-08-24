from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from universal_agent.capability import (
    CapabilityUnavailableError,
    UnknownCapabilityError,
)
from universal_agent.coordination import (
    ResourceConflictError,
    ResourceLock,
    ResourceVersionConflictError,
)
from universal_agent.core import (
    ActionId,
    CapabilityDefinition,
    Decision,
    DomainIdentity,
    ErrorCode,
    JsonMapping,
    JsonValue,
    Observation,
    ObservationStatus,
    PendingAction,
    PolicyContext,
    PolicyEffect,
    SideEffect,
    ToolCall,
    ToolDefinition,
    immutable_json,
    new_action_id,
)
from universal_agent.domain import ActionArgumentContext, RuntimeComponents
from universal_agent.observation import ObservationFactory
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.security import SecretProvider, SecretResolutionReport
from universal_agent.tools import ToolRuntime

EmitFn = Callable[[str, ActionId | None, dict[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionRejected:
    """The action never reached a tool: unknown capability, policy deny, or drift."""

    error_code: ErrorCode
    reason: str


@dataclass(frozen=True, slots=True)
class ConfirmationRequired:
    pending: PendingAction
    reason: str


@dataclass(frozen=True, slots=True)
class ActionObserved:
    pending: PendingAction
    observation: Observation


ActionOutcome = ActionRejected | ConfirmationRequired | ActionObserved


@dataclass(slots=True)
class ActionExecutor:
    """Turns a decision into an observation.

    Owns capability resolution, policy checks and tool invocation. It never
    decides whether the goal ends; it reports what happened and lets the runtime
    choose the transition.
    """

    components: RuntimeComponents
    environment: JsonMapping
    secret_provider: SecretProvider | None = None
    secret_resolution: SecretResolutionReport | None = None
    _tool_runtime: ToolRuntime = field(init=False)
    _observations: ObservationFactory = field(init=False)

    def __post_init__(self) -> None:
        self._tool_runtime = ToolRuntime(
            self.components.tools,
            secret_provider=self.secret_provider,
            secret_resolution=self.secret_resolution,
        )
        self._observations = ObservationFactory()

    async def prepare(
        self,
        session: SessionRuntimeState,
        decision: Decision,
        emit: EmitFn,
    ) -> ActionOutcome:
        try:
            resolution = self.components.resolver.resolve_registration(decision.capability or "")
        except UnknownCapabilityError as exc:
            return ActionRejected(ErrorCode.UNKNOWN_CAPABILITY, str(exc))
        except CapabilityUnavailableError as exc:
            return ActionRejected(ErrorCode.NO_CAPABILITY_TOOL, str(exc))
        capability = resolution.capability
        tool = resolution.tool
        domain_name = resolution.capability_domain.name if resolution.capability_domain else ""
        domain_version = (
            resolution.capability_domain.version if resolution.capability_domain else ""
        )
        decision, enriched_argument_names = self._enrich_decision_arguments(
            session,
            decision,
            capability,
            tool.definition,
            resolution.capability_domain,
        )
        parameters_hash = _action_parameters_hash(decision)
        resource_key, resource_version = _resource_metadata(
            side_effect=tool.definition.side_effect,
            capability=capability.name,
            target=decision.target,
            arguments=decision.arguments,
        )
        if tool.definition.side_effect is not SideEffect.NONE and not resource_key:
            return ActionRejected(
                ErrorCode.VALIDATION_ERROR,
                "side-effecting action requires a resource identity",
            )
        pending = PendingAction(
            action_id=new_action_id(),
            capability=capability.name,
            tool_name=tool.definition.name,
            target=decision.target,
            arguments=decision.arguments,
            domain_name=domain_name,
            domain_version=domain_version,
            idempotency_key=_idempotency_key(
                session.state.session_id,
                session.state.current_task.id,
                parameters_hash,
            ),
            parameters_hash=parameters_hash,
            attempt=1,
            resource_key=resource_key,
            resource_version=resource_version,
        )
        if enriched_argument_names:
            await emit(
                "ActionArgumentsEnriched",
                pending.action_id,
                {
                    "capability": capability.name,
                    "tool_name": tool.definition.name,
                    "argument_names": enriched_argument_names,
                },
            )
        await emit(
            "CapabilityResolved",
            pending.action_id,
            {
                "capability": capability.name,
                "tool_name": tool.definition.name,
                "domain": domain_name,
                "domain_version": domain_version,
                "idempotency_key": pending.idempotency_key,
                "parameters_hash": pending.parameters_hash,
                "attempt": pending.attempt,
                "resource_key": pending.resource_key,
                "resource_version": pending.resource_version,
            },
        )
        return await self.execute(session, pending, emit, confirmed=False)

    def _enrich_decision_arguments(
        self,
        session: SessionRuntimeState,
        decision: Decision,
        capability: CapabilityDefinition,
        tool: ToolDefinition,
        domain_identity: DomainIdentity | None,
    ) -> tuple[Decision, tuple[str, ...]]:
        providers = self.components.action_argument_providers_for_domain(domain_identity)
        if not providers:
            return decision, ()
        arguments = dict(decision.arguments)
        added: list[str] = []
        for provider in providers:
            if provider.capability_names and decision.capability not in provider.capability_names:
                continue
            contextual_decision = replace(decision, arguments=immutable_json(arguments))
            provided = provider.provide(
                ActionArgumentContext(
                    session.state.session_id,
                    session.state.goal,
                    session.state.current_task,
                    contextual_decision,
                    capability,
                    tool,
                    session.world(),
                )
            )
            for key, value in provided.items():
                if key in arguments:
                    continue
                arguments[key] = value
                added.append(key)
        if not added:
            return decision, ()
        return replace(decision, arguments=immutable_json(arguments)), tuple(dict.fromkeys(added))

    async def execute(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
        *,
        confirmed: bool,
    ) -> ActionOutcome:
        state = session.state
        try:
            resolution = self.components.resolver.resolve_registration(pending.capability)
        except (UnknownCapabilityError, CapabilityUnavailableError) as exc:
            return ActionRejected(ErrorCode.INVALID_STATE, str(exc))
        capability = resolution.capability
        tool = resolution.tool
        if tool.definition.name != pending.tool_name:
            return ActionRejected(
                ErrorCode.INVALID_STATE,
                "pending action tool resolution changed",
            )
        domain = resolution.capability_domain
        if domain is not None and (
            pending.domain_name != domain.name or pending.domain_version != domain.version
        ):
            return ActionRejected(
                ErrorCode.INVALID_STATE,
                "pending action domain resolution changed",
            )
        pending = _ensure_resource_metadata(pending, tool.definition.side_effect)
        if tool.definition.side_effect is not SideEffect.NONE and not pending.resource_key:
            return ActionRejected(
                ErrorCode.VALIDATION_ERROR,
                "side-effecting action requires a resource identity",
            )
        policy_result = self.components.policy_engine.check(
            PolicyContext(
                session_id=state.session_id,
                goal_id=state.goal.id,
                task_id=state.current_task.id,
                action_id=pending.action_id,
                capability=capability,
                tool=tool.definition,
                target=pending.target,
                arguments=pending.arguments,
                environment=self.environment,
                confirmed=confirmed,
            )
        )
        await emit(
            "PolicyChecked",
            pending.action_id,
            {
                "effect": policy_result.effect.value,
                "policy": policy_result.policy_name,
                "capability": capability.name,
                "tool_name": tool.definition.name,
                "side_effect": tool.definition.side_effect.value,
                "risk": tool.definition.risk.value,
            },
        )
        if policy_result.effect is PolicyEffect.DENY:
            return ActionRejected(ErrorCode.POLICY_DENIED, policy_result.reason)
        version_check = await self._check_resource_version(pending, emit)
        if isinstance(version_check, ActionRejected):
            return version_check
        if policy_result.effect is PolicyEffect.REQUIRE_CONFIRMATION:
            lock = await self._acquire_resource_lock(session, pending, emit)
            if isinstance(lock, ActionRejected):
                return lock
            state.pending_action = pending
            return ConfirmationRequired(pending, policy_result.reason)
        state.pending_action = None
        lock = await self._acquire_resource_lock(session, pending, emit)
        if isinstance(lock, ActionRejected):
            return lock
        try:
            return await self._invoke(session, pending, emit)
        finally:
            await self._release_resource_lock(lock, emit)

    async def _invoke(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
    ) -> ActionObserved:
        state = session.state
        tool = self.components.tools.resolve(pending.tool_name)
        call = ToolCall(
            action_id=pending.action_id,
            tool_name=pending.tool_name,
            capability=pending.capability,
            arguments=pending.arguments,
            target=pending.target,
            domain_name=pending.domain_name,
            domain_version=pending.domain_version,
            idempotency_key=pending.idempotency_key,
            parameters_hash=pending.parameters_hash,
            attempt=pending.attempt,
            resource_key=pending.resource_key,
            resource_version=pending.resource_version,
        )
        await emit(
            "ActionStarted",
            call.action_id,
            {
                "tool_name": call.tool_name,
                "capability": call.capability,
                "side_effect": tool.definition.side_effect.value,
                "risk": tool.definition.risk.value,
                "domain": call.domain_name,
                "domain_version": call.domain_version,
                "idempotency_key": call.idempotency_key,
                "parameters_hash": call.parameters_hash,
                "attempt": call.attempt,
                "resource_key": call.resource_key,
                "resource_version": call.resource_version,
            },
        )
        tool_result = await self._tool_runtime.execute(call)
        await emit(
            "ActionCompleted",
            call.action_id,
            {
                "status": tool_result.status.value,
                "error_code": (
                    None if tool_result.error_code is None else tool_result.error_code.value
                ),
            },
        )
        if tool_result.status is ObservationStatus.SUCCEEDED:
            await self._update_resource_version(pending, tool_result.output, emit)
        observation = self._observations.from_tool_result(
            task_id=state.current_task.id,
            call=call,
            result=tool_result,
        )
        state.observations.append(observation)
        await emit(
            "ObservationReceived",
            observation.action_id,
            {"observation_id": observation.id, "status": observation.status.value},
        )
        return ActionObserved(pending, observation)

    async def _check_resource_version(
        self,
        pending: PendingAction,
        emit: EmitFn,
    ) -> ActionRejected | None:
        if not pending.resource_key:
            return None
        current = self.components.resource_versions.current(pending.resource_key)
        try:
            check = self.components.resource_versions.verify(
                resource_key=pending.resource_key,
                expected_version=pending.resource_version,
            )
        except ResourceVersionConflictError as exc:
            await emit(
                "ResourceVersionChecked",
                pending.action_id,
                {
                    "resource_key": pending.resource_key,
                    "resource_version": pending.resource_version,
                    "current_resource_version": current,
                    "matched": False,
                    "reason": str(exc),
                },
            )
            await emit(
                "ResourceConflictDetected",
                pending.action_id,
                {
                    "resource_key": pending.resource_key,
                    "resource_version": pending.resource_version,
                    "current_resource_version": current,
                    "reason": str(exc),
                },
            )
            return ActionRejected(ErrorCode.RESOURCE_CONFLICT, str(exc))
        await emit(
            "ResourceVersionChecked",
            pending.action_id,
            {
                "resource_key": check.resource_key,
                "resource_version": check.expected_version,
                "current_resource_version": check.current_version,
                "matched": True,
                "reason": check.reason,
            },
        )
        return None

    async def _update_resource_version(
        self,
        pending: PendingAction,
        output: JsonMapping,
        emit: EmitFn,
    ) -> None:
        if not pending.resource_key:
            return
        version = _resource_version(output)
        if version is None:
            return
        self.components.resource_versions.set_current(pending.resource_key, version)
        await emit(
            "ResourceVersionUpdated",
            pending.action_id,
            {
                "resource_key": pending.resource_key,
                "resource_version": version,
            },
        )

    async def release_pending_resource(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
    ) -> None:
        """Release a pending confirmation lock after rejection or cancellation."""

        if not pending.resource_key:
            return
        lock = ResourceLock(
            pending.resource_key,
            pending.action_id,
            session.state.session_id,
            session.state.current_task.id,
        )
        await self._release_resource_lock(lock, emit)

    async def _acquire_resource_lock(
        self,
        session: SessionRuntimeState,
        pending: PendingAction,
        emit: EmitFn,
    ) -> ResourceLock | ActionRejected | None:
        if not pending.resource_key:
            return None
        already_owned = self.components.resource_locks.is_owned_by(
            resource_key=pending.resource_key,
            action_id=pending.action_id,
            session_id=session.state.session_id,
            task_id=session.state.current_task.id,
        )
        try:
            lock = self.components.resource_locks.acquire(
                resource_key=pending.resource_key,
                action_id=pending.action_id,
                session_id=session.state.session_id,
                task_id=session.state.current_task.id,
            )
        except ResourceConflictError as exc:
            await emit(
                "ResourceConflictDetected",
                pending.action_id,
                {
                    "resource_key": pending.resource_key,
                    "resource_version": pending.resource_version,
                    "reason": str(exc),
                },
            )
            return ActionRejected(ErrorCode.RESOURCE_CONFLICT, str(exc))
        if not already_owned:
            await emit(
                "ResourceLockAcquired",
                pending.action_id,
                {
                    "resource_key": lock.resource_key,
                    "resource_version": pending.resource_version,
                },
            )
        return lock

    async def _release_resource_lock(
        self,
        lock: ResourceLock | None,
        emit: EmitFn,
    ) -> None:
        if lock is None:
            return
        self.components.resource_locks.release(lock)
        await emit(
            "ResourceLockReleased",
            lock.action_id,
            {"resource_key": lock.resource_key},
        )


def _action_parameters_hash(decision: Decision) -> str:
    payload = {
        "arguments": _canonical_json(decision.arguments),
        "capability": decision.capability,
        "target": decision.target,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idempotency_key(session_id: object, task_id: object, parameters_hash: str) -> str:
    return f"{session_id}:{task_id}:{parameters_hash[:16]}"


def _ensure_resource_metadata(
    pending: PendingAction,
    side_effect: SideEffect,
) -> PendingAction:
    if side_effect is SideEffect.NONE or pending.resource_key:
        return pending
    resource_key, resource_version = _resource_metadata(
        side_effect=side_effect,
        capability=pending.capability,
        target=pending.target,
        arguments=pending.arguments,
    )
    if not resource_key and resource_version is None:
        return pending
    return replace(pending, resource_key=resource_key, resource_version=resource_version)


def _resource_metadata(
    *,
    side_effect: SideEffect,
    capability: str,
    target: str | None,
    arguments: JsonMapping,
) -> tuple[str, str | None]:
    if side_effect is SideEffect.NONE:
        return "", None

    resource_key = _resource_key(capability, target, arguments)
    resource_version = _resource_version(arguments)
    return resource_key, resource_version


def _resource_key(
    capability: str,
    target: str | None,
    arguments: JsonMapping,
) -> str:
    if isinstance(target, str) and target.strip():
        return target.strip()
    for key in ("resource_key", "resource", "target", "id"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    namespace = arguments.get("namespace")
    name = arguments.get("name")
    if isinstance(namespace, str) and namespace.strip() and isinstance(name, str) and name.strip():
        return f"{namespace.strip()}/{name.strip()}"
    if isinstance(name, str) and name.strip():
        return f"{capability}:{name.strip()}"
    return ""


def _resource_version(arguments: JsonMapping) -> str | None:
    for key in ("resource_version", "resourceVersion", "version", "revision"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _canonical_json(value: JsonValue | object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_canonical_json(item) for item in value]
    return str(value)
