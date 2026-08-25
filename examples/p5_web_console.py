from __future__ import annotations

import asyncio

from universal_agent.agentd import AgentdApp, HttpRequest
from universal_agent.cli import LOCAL_PROFILE_NAME, build_default_service
from universal_agent.core import JsonMapping, JsonValue, immutable_json


def session_request_body() -> JsonMapping:
    goal: dict[str, JsonValue] = {
        "description": "Verify workload health from the Web Console example",
        "success_criteria": [{"key": "healthy", "expected": True}],
    }
    task: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": ["healthy"],
    }
    return immutable_json(
        {
            "profile": LOCAL_PROFILE_NAME,
            "goal": goal,
            "task": task,
        }
    )


async def main() -> None:
    app = AgentdApp(build_default_service())
    created = await app.handle(HttpRequest("POST", "/v1/sessions", session_request_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    response = await app.handle(
        HttpRequest("GET", f"/console?session_id={session_id}&event_limit=20")
    )
    detail = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}?event_limit=20"))
    evidence = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}/evidence"))
    world = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}/world"))
    domain = await app.handle(HttpRequest("GET", "/console/domains/kubernetes/0.2.0"))
    profiles = await app.handle(HttpRequest("GET", "/console/profiles"))
    evaluations = await app.handle(HttpRequest("GET", "/console/evaluations"))
    assert response.text_body is not None
    assert detail.text_body is not None
    assert evidence.text_body is not None
    assert world.text_body is not None
    assert domain.text_body is not None
    assert profiles.text_body is not None
    assert evaluations.text_body is not None
    print(f"status={response.status_code}")
    print(response.headers["content-type"])
    print("\n".join(response.text_body.splitlines()[:20]))
    print(f"detail_status={detail.status_code}")
    print("\n".join(detail.text_body.splitlines()[:12]))
    print(f"evidence_status={evidence.status_code}")
    print(f"world_status={world.status_code}")
    print(f"domain_status={domain.status_code}")
    print(f"profiles_status={profiles.status_code}")
    print(f"evaluations_status={evaluations.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
