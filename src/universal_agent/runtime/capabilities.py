from __future__ import annotations

from universal_agent.capability import CapabilityUnavailableError, UnknownCapabilityError
from universal_agent.core import (
    CapabilityDefinition,
    CapabilityInputContract,
    Decision,
    DecisionType,
    ErrorCode,
    validate_argument_contract,
)
from universal_agent.domain import RuntimeComponents


class CapabilityAdvisor:
    """Resolves what the model may choose and validates its choice.

    Reads capabilities and their resolved tools to build the capability context
    for the context compiler, and re-checks a decision against that context
    before the runtime acts. It owns no execution and no state.
    """

    def __init__(self, components: RuntimeComponents) -> None:
        self._capabilities = components.capabilities
        self._resolver = components.resolver

    def context(
        self,
    ) -> tuple[tuple[CapabilityDefinition, ...], tuple[CapabilityInputContract, ...]]:
        executable: list[CapabilityDefinition] = []
        contracts: list[CapabilityInputContract] = []
        for capability in self._capabilities.all():
            try:
                resolution = self._resolver.resolve_registration(capability.name)
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

    def validate_decision_context(
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
            self._capabilities.resolve_registration(capability)
        except UnknownCapabilityError as exc:
            return ErrorCode.UNKNOWN_CAPABILITY, str(exc)
        return (
            ErrorCode.NO_CAPABILITY_TOOL,
            f"capability is not executable in current context: {capability}",
        )
