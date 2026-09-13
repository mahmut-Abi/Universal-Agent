# P5 User Interface Roadmap Proposal

> Date: 2026-09-12
> Status: PROPOSAL — awaiting review before implementation
> Roadmap reference: `AGENTS.md` §13 P5, §19 ("The Web UI must not become a prerequisite for validating the Runtime")

## 1. Survey: What Already Exists

The repository is further along P5 than the phase list suggests. All four P5
surfaces have a working foundation:

### 1.1 Web Console (`src/universal_agent_web/` + `agentd/console_routes.py`)

- Static client package (pure HTML/JS/CSS, ~635 lines, no build step, optional
  install — agentd serves a fallback page pointing at the Runtime API when it
  is absent).
- agentd `/console` routes already expose: sessions, session detail, evidence,
  world, evaluations, doctor, distributed, multi-agent, domains,
  domain-packages, profiles, settings, and operator actions
  (pause/resume/cancel) dispatched through the same RuntimeService methods the
  CLI uses — policy checks and the pending-action confirmation boundary stay
  identical across surfaces.
- This means Session Explorer, Domain Manager UI, World Model Explorer, and
  Evaluation Console all have **minimal but real** web representations.

### 1.2 TUI (`src/universal_agent_tui/`, ~1973 lines)

- `ua tui` entry point with `--session-id` / `--session-limit`.
- Session list + session inspection + run views; thin-client against agentd
  (`tui_remote.py`) and local rendering paths.

### 1.3 Evaluation Console (`universal_agent/evaluation/console.py`)

- Snapshot builder + terminal text rendering; 4 unit tests including escaping
  and empty-report handling.

### 1.4 Gaps Found

| Gap | Evidence |
| --- | --- |
| No dedicated web-console route tests | `tests/unit/test_web_console.py` referenced in `docs/developer-guide.md` "Common Test Targets" does **not exist** (stale doc reference; only `test_evaluation_console.py` covers one console view) |
| TUI test coverage is thin | 1 test in `tests/unit/test_tui.py` for ~2000 lines of TUI code |
| Explorer depth | Console views render snapshots; drill-down (session event timeline, world entity/relation navigation, evidence detail) is minimal |

## 2. Proposed Backlog Items

Each item is independent, committable, and follows the same discipline as the
optimization backlog (tests + docs + no kernel/domain boundary violations).
Suggested priorities: P5-CORE first, then P5-EXPLORER, then P5-TUI.

### [P5-CORE-001] Fix stale web-console test reference; add console route test suite

- Why: `docs/developer-guide.md` tells contributors to run a test file that
  does not exist; console routes (16+ views) have no dedicated suite.
- Done when:
  - Developer guide references real test paths.
  - New `tests/unit/test_agentd_console.py` covers route matching, fallback
    page when client package absent, asset serving, and at least one operator
    action dispatch per runtime state (pause/resume/cancel).
  - Full gate passes (ruff/mypy/pytest).

### [P5-CORE-002] Console session event timeline drill-down

- Why: Session Explorer should let an operator answer "why did the agent do
  this?" without the CLI (`session show` already renders a raw timeline).
- Done when:
  - `/console/sessions/{id}` renders the event timeline with
    decision/observation/evidence correlation via stable IDs
    (goal_id/task_id/action_id/observation_id/evidence_id).
  - JSON payloads are escaped; no secrets rendered (reuse secret redaction).
  - Unit tests cover timeline rendering and redaction.

### [P5-CORE-003] Console world model explorer navigation

- Why: World Model Explorer currently shows a snapshot; operators need
  entity/relation navigation consistent with `world_views` projections.
- Done when:
  - Entity detail view lists relations, contributing domains, and supporting
    evidence IDs from the shared world model.
  - Cross-domain conflicts and their resolutions are visible when present.
  - Tests cover entity/relation/conflict rendering.

### [P5-CORE-004] Console evidence drill-down

- Why: Evidence is the verification backbone; the console should support
  inspecting individual evidence records and their links to actions.
- Done when:
  - Evidence list filters by session/goal; detail view shows claim, source,
    action linkage, and verification status.
  - Tests cover list/detail rendering and empty states.

### [P5-TUI-001] TUI session event timeline parity with console

- Why: TUI is the terminal-native operator surface; it should expose the same
  session timeline the console does, from the same JSON payloads.
- Done when:
  - TUI session view renders the event timeline using
    `session_representations` bodies (no second projection layer).
  - Tests cover timeline projection and empty/long-history handling.

### [P5-TUI-002] TUI world/evidence read views

- Why: round out TUI so operators never need the web console for inspection.
- Done when:
  - TUI world snapshot view (entities/relations/conflicts) and evidence list
    view render from the same JSON bodies as the console.
  - Tests cover both views.

### Non-goals (this phase)

- No new frontend framework or build tooling — the static package stays
  build-free; server-rendered JSON + small JS only.
- No web UI as acceptance criterion for runtime features (AGENTS.md §19).
- No multi-user/auth work in the UI (that is UA-PROD-003, blocked).

## 3. Verification Plan

- Per item: `uv run ruff format --check src tests examples && uv run ruff check . && uv run mypy && uv run pytest tests/ -q`.
- Smoke: start agentd, curl each new/changed console route, confirm 200 and
  redacted payloads; run `ua tui` against a demo session.
- Each completed item: update `docs/revision/README.md` (new P5 section) with
  `[x]` + Completion Log entry, one commit per item.

## 4. Open Questions for Review

1. Priority order agreed (P5-CORE → P5-EXPLORER depth → P5-TUI)?
2. Should console operator actions (pause/resume/cancel) get a confirmation
   banner in the web UI mirroring the CLI banner added in UA-P1-008?
3. Any appetite for a `world model` diff view (snapshot A vs B) now, or defer?
