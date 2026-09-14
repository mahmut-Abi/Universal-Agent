# P0 Golden Path Validation Record (2026-09-11)

Spec: `docs/specs/Universal-Agent-P0-spec.md` (P0 产品化任务与验收标准).
Decisions: `docs/product.md` (D1 `agent` entry kept, D2 generic `default`
profile, D3 advanced commands labeled, D4 config layout, D5 text-default output).

## What was implemented (Phase order per spec §28)

1. **CLI normalization** — `ua` installed as an alias of `agent`
   (`pyproject.toml [project.scripts]`); `--help` lists Golden Path commands
   (`init/run/session/config/profile/doctor`) first with plain-language help;
   advanced commands keep working and are labeled advanced.
2. **Configuration** — `agent init` writes `universal-agent/profile.json`
   (AgentProfile) + `universal-agent/config.json` (readable settings: profile,
   model, policy mode, runtime, domains). Discovery order:
   `$AGENT_CONFIG_DIR` → `./universal-agent/profile.json` →
   `~/.universal-agent/profile.json` (`default_profile_config_path`). `init` is
   idempotent (`status=reused`), `--force` resets with `.bak` backups. File
   store by default so Sessions survive restarts.
3. **init / doctor / config** — `init` non-interactive, flag/env-driven, never
   requires API keys; `doctor` checks Environment, Configuration, Model,
   Runtime, Profiles, Domains, Policy and prints `Try:` fixes, exit 1 on
   failures (`--fail-on`, default `error`); bare `agent config` prints the
   effective config (secrets never printed — only references + availability).
4. **run / session / profile** — `agent run "goal"` works without a profile
   positional or a running server (embedded agentd runtime), prints
   `Agent started / Session / Status / Duration / Steps / Tool calls / Evidence`
   and explicit next steps for waiting/failed runs (`--output json` for
   machines); `session list/show` human output with friendly statuses
   (`success/failed/waiting/cancelled`), timeline and counts; `profile
   list/show` human output; `--profile`/positional profile both supported.
5. **Facade** — `universal_agent.Agent` (`Agent.from_profile(...)` /
   `agent.run(...)`, resume/pause/cancel/sessions/session/events) assembled via
   `RuntimeHost`; documented in `docs/RUNTIME_CONTRACT.md` §Agent.
6. **Tests** — CLI smoke coverage: help/order, init idempotency+backups,
   doctor sections + Try hints + exit codes, config secret masking,
   run/session/profile human + JSON output; Golden Path integration
   (`tests/integration/test_p0_golden_path.py`); facade + policy-denial +
   confirmation/persistence tests (`tests/integration/test_facade_golden_path.py`);
   unit suite (`tests/unit/test_p0_config_and_views.py`). CI needs no real
   LLM: `FakeModel` (= `ScriptedModelAdapter`) + fake Kubernetes backend.
7. **README** — rewritten per spec §16: first screen = what/why/CLI/Web/agentd +
   Golden Path quick start; configuration/profiles/sessions/domains/policy
   sections; Web positioned as observation UI; agentd positioned as server
   deployment; architecture after Quick Start.

## Clean-room validation (spec §17/§27)

Fresh copy of the repo, empty `$HOME`, `uv sync` (creates `.venv`), then:

```text
uv run ua --help    → usage shows golden-path commands first; ua/agent alias OK
uv run ua init      → "Universal Agent setup complete." (profile.json + config.json)
uv run ua init      → second run reuses config (idempotent), exit 0
uv run ua doctor    → all sections ✓ (Environment/Configuration/Model/Runtime/
                       Profiles/Domains/Policy), Status: ok, exit 0
uv run ua run "Analyze the demo workload" --success healthy=true
                    → Agent started/Session/…/Status: success
                       Session: session-c3add0ea…  Duration: 0.05s
                       Steps: 2  Tool calls: 1  Evidence: 3
uv run ua session list    → human table, the run's session present (status success)
uv run ua session show …  → timeline (DomainActivated…GoalCompleted),
                             Evidence: 3, Actions: 1
uv run ua config          → Model/Runtime/Domains/Secrets/Config view
uv run ua profile list|show default → default + kubernetes@0.2.0
```

All commands completed with exit 0 without any API key, cluster, or manually
started server.

## Quality gates

- `uv run ruff format --check src tests examples` — clean (478 files)
- `uv run ruff check .` — clean
- `uv run mypy` — clean (478 source files)
- `uv run pytest tests/` — **1437 passed, 3 skipped** (skips are live-cluster
  tests that require real credentials)
- `pyright` (repo `pyrightconfig.json`, standard mode) — 0 errors on the
  packages touched by this pass (`universal_agent_cli`, `universal_agent.model`,
  `universal_agent.facade`)

## Spec DoD checklist status (§26)

- Product: single CLI entry (`agent`/`ua`), README first screen, Quick Start,
  config locations, init/doctor/run/session/profile, Web + agentd positioning — done.
- Runtime: Agent facade, session auto-create + query + persistence, policy as
  hard boundary, evidence/evaluation/recovery reuse (no new runtime semantics) — done.
- Testing: CLI smoke tests, FakeModel, Golden Path integration test, policy
  denial + confirmation tests, session persistence tests, no real-LLM CI — done.
- Documentation: README rewrite, configuration/profile/session/CLI docs,
  `docs/RUNTIME_CONTRACT.md`, Web/agentd positioning — done.

## Known follow-ups (not P0 scope)

- `agent run` against a real model/cluster requires explicit opt-in via
  `agent init --model-provider … / --domain-backend …` (unchanged behavior).
- Web Console remains read-only snapshots (P5 operator controls are a later
  phase per the remaining-TODO list).
- `docs/product.md` §5 records the D4/D5 decisions for future phases.
