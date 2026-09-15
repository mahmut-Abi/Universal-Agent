# AGENTS.md Scope Reconciliation Decision（2026-09-15）

Backlog item: `UA-AUDIT-003`（`docs/revision/README.md`）
Source audit: `docs/revision/2026-09-15-strict-audit.md`（A3）

## Context

The 2026-09-15 strict audit found that `AGENTS.md` §13（P0→P7 增量路线图）and
§19（"What NOT To Build Prematurely"）contradict the repository state: the
P0–P7 phases all have implemented foundations（`multi_agent/`、`distributed/`、
`ecosystem/`、TUI、Web、Domain SDK），and `docs/index.md` documents that scope
openly. Two normative documents disagreeing is worse than either content.

## Options Considered

1. **修订 roadmap**：把 §13 改为"能力矩阵 + 成熟度标注"，承认现状。
   - Pro: 文档与现实一致；给用户/贡献者正确预期。
   - Con: 单独使用会弱化 §19 的纪律（看起来"什么都可以做"）。

2. **冻结声明**：保持 §13/§19 原文，追加一节声明 P4–P7 为 frozen/experimental。
   - Pro: 保留"先证明核心循环"的纪律。
   - Con: 单独使用会留下"路线图 vs 仓库"的矛盾未被承认。

## Decision

**两者结合**（combined）：

- §13 gains a "Scope status" block: P0–P3 **stable**, P3.5–P3.7 **beta**,
  P4/P5/P6/P7 **experimental, frozen**.
- §19 gains a freeze note: the "not prematurely" list remains the default
  posture for NEW work; existing experimental layers accept bug fixes,
  security fixes, and test upkeep only.
- The core-loop-first principle（§16 metrics: "Can the Agent reliably complete
  real tasks?"）is the gate for unfreezing any layer.

## Consequences

- New capability work defaults to the stable/beta layers（World Model,
  Decision Quality, Evidence, Recovery, Context, Evaluation — per §24 North
  Star）.
- Any unfreeze of P4–P7 requires a new decision record with evidence that the
  core loop is proven and a concrete user value.
- `docs/index.md` maturity labels should track these definitions.

## Status

Accepted, 2026-09-15. Implemented in `AGENTS.md` §13/§19.

## Amendment (2026-09-15, same day): P5 web rebuild exception

The author directed a standalone **Node.js web tier** (`web/`): a configurable,
multi-session chat UI deployed via its own Docker Compose service, consuming the
agentd HTTP API only — never embedded in the agentd process. This is an explicit
author exception to the P5 freeze for this work item, alongside the client/server
separation hardening (thin-client config file `server.url`, `AGENT_API_URL` env).

- Server: agentd in Docker (existing image + compose `agentd` service).
- Client: CLI thin-client mode is now config-driven (`universal-agent/config.json`
  `server.url` > `AGENT_API_URL` env > `--api-url` flag).
- Web: `web/` Node.js (zero npm dependencies) + `web/Dockerfile` + compose `web`
  service; replaces the agentd-embedded console as the interactive surface.
