# 毒舌项目分析（Critical Review）

> 日期：2026-08-29
> 视角： senior Agent Runtime engineer，按 AGENTS.md 标准审视当前实现
> 结论：用「架构完美主义」逃避「产品可用性」。

## 一、核心矛盾：亲手打了自己脸

AGENTS.md 第 19 节《What NOT To Build Prematurely》明令禁止先做：
multi-agent 编排、分布式运行时、marketplace、ecosystem packaging。

但仓库已实现：

- `src/universal_agent/multi_agent/` —— P4 全套（orchestrator / conflict / merge / registry）
- `src/universal_agent/distributed/` —— P6 全套（scheduler / worker / queue / lock / coordinator，`queue.py` 921 行）
- `src/universal_agent/ecosystem/`、`domain/package_*` —— P7 全套

P0 的「一个真实能跑完的 Kubernetes 修复场景」尚未证明，就已把 P7 生态注册表、签名校验、安装计划写完。
这是典型的 **用广度假装深度**。

## 二、元数据税：45 个 View、36 个 encode/decode、330 个导出符号

`src/universal_agent/__init__.py` 681 行，导出约 330 个名字。Runtime 内核不该有这种表面积。

大量代码是自指性的元数据税：

- `verify_domain_package` → `verify_domain_package_registry` → `EcosystemCatalog.verify` → `EcosystemRegistryManifest` → `install_ecosystem`
- `encode_*` / `decode_*` 36 处成对出现

这些「verify the manifest that verifies the registry that verifies the catalog」的东西，
**没有任何一条在帮 Agent 完成真实任务**。它们验证的是「元数据格式对不对」，不是「Agent 能不能修好一个 CrashLoop 的 Pod」。

## 三、106 个 examples，但没有一个「真刀真枪」

examples 目录 106 个文件，全是 fixture-backed / fake backend。README 自己承认：

> no real cluster is accessed unless a caller explicitly wires `KubectlBackend`

`kubectl.py` 虽存在，但 74 个测试文件 / 35k 行测试几乎确定跑的是假后端。
35k 行测试 vs 46k 行源码，**至少一半在验证 payload round-trip，而非 Agent 真能干活**。

按 AGENTS.md 第 14 节，第一个端到端场景应是：

> "Find out why this Deployment is unhealthy and fix it if it is safe."

目前没看到任何真实集群、真实 `kubectl` 的集成测试。证明了框架，没证明能力。

## 四、巨型文件违背自己的第 17.1 条

AGENTS.md 17.1：「Prefer small modules」「Avoid giant files such as `agent.py`」。实际：

| 文件 | 行数 |
|---|---|
| `distributed/queue.py` | 921 |
| `kubernetes/cli_reports.py` | 919 |
| `ecosystem/catalog.py` | 914 |
| `agentd/app.py` | 911 |
| `evaluation/harness.py` | 899 |
| `service/runtime.py` | 877 |
| `cli.py` | 859 |
| `runtime/agent.py` | 808 |

`runtime/agent.py` 808 行——正是「整个 runtime 塞一个文件」反模式发生在核心里。

## 五、优化意见（按 ROI 排序）

1. **砍掉 P4/P6/P7 的验收仪式代码，先把 P0–P2 做扎实。**
   在真实 k8s 上跑通 unhealthy deployment 的 diagnose → fix → verify 端到端。
   这是唯一能证明不是空中楼阁的事。

2. **合并 45 个 View / 36 个 codec。**
   用 `@dataclass` + 统一 `to_projection()` 协议替代手工 pairwise 编解码；
   `__init__.py` 只导出 facade（`RuntimeService` / `UniversalAgentRuntime`），
   不摊开 330 个符号。

3. **给测试分两类并公开比例**：`behavior/`（真行为）vs `contract/`（payload round-trip）。
   若 behavior 占比 < 20%，覆盖率数字毫无意义。

4. **拆 `runtime/agent.py` 与 `service/runtime.py`**（808 / 877 行）。已违反自身规范，是维护雷。

5. **承认 `verify/registry/ecosystem` 是沉没成本。**
   设计不坏，但时机错误。核心能力被证明前，生态/签名/安装计划都是装饰品。
   标 `experimental` 冻结，别再加功能。

6. **写一个能跑的真 demo 脚本，而非再写一份 `docs/revision/2026-08-26-project-status.md`。**
   已写 4 份状态文档，没有一份能替代一个真跑通的场景。

## 一句话

架构文档像博士论文，运行时却还没在真实集群上修好过一个 Pod。
先证明它能干活，再来搭帝国。

## 六、测试分类实测（2026-08-29）

按第 3 条要求，把 `tests/**/test_*.py` 共 892 个测试函数按关键字启发式分为三类，
并写入 pytest marker（`behavior` / `contract` / `unit`），可用
`pytest -m behavior` / `-m contract` / `-m unit` 单独运行。

| 类别 | 数量 | 占比 | 含义 |
|---|---|---|---|
| behavior | 229 | 25.7% | 断言 Agent 真实运行结果（goal/task 完成、world model、policy、recovery、evaluation） |
| contract | 257 | 28.8% | 断言 payload / 序列化 / View round-trip 形状 |
| unit | 406 | 45.5% | 内部 helper / parser / config / 纯逻辑的单测，既非行为也非契约 |

结论修正：

- 原文第 3 条「至少一半测试在验证 payload round-trip」**被高估**：contract 仅 28.8%。
- 但原文核心判断成立且更尖锐：**真正断言 Agent 行为的测试只有 25.7%**，是三类里最少的一类；
  最大头是 45.5% 的内部单测（验证管道而非能力）。
- 按原文自身红线「behavior < 20% 则覆盖率无意义」：25.7% 刚过线，但覆盖率仍偏「广度（管道）」而非「深度（能力）」。

分类器：`tools/classify_tests.py`（启发式，边界为近似；per-file 明细见运行时 `--report`）。
此分类不改变任何测试行为，仅为可观测性与后续加测提供导向。
