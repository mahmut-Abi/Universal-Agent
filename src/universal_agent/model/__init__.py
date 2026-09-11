from universal_agent.model.adapter import (
    ModelAdapter,
    ModelUsage,
    ModelUsageProvider,
    ScriptedModelAdapter,
    model_usage,
)
from universal_agent.model.http import (
    HttpxJsonHttpTransport,
    JsonHttpModelAdapter,
    JsonHttpModelError,
    JsonHttpModelTransport,
    OpenAIChatCompletionsModelAdapter,
    OpenAIModelTransport,
    OpenAIResponsesModelAdapter,
    OpenAISdkModelTransport,
    StdlibJsonHttpTransport,
)

# P0 Golden Path alias: `FakeModel` names the deterministic offline model used
# by tests, doctor and the default profile (no real LLM required).
FakeModel = ScriptedModelAdapter


__all__ = [
    "FakeModel",
    "HttpxJsonHttpTransport",
    "JsonHttpModelAdapter",
    "JsonHttpModelError",
    "JsonHttpModelTransport",
    "ModelAdapter",
    "ModelUsage",
    "ModelUsageProvider",
    "OpenAIChatCompletionsModelAdapter",
    "OpenAIModelTransport",
    "OpenAIResponsesModelAdapter",
    "OpenAISdkModelTransport",
    "ScriptedModelAdapter",
    "StdlibJsonHttpTransport",
    "model_usage",
]
