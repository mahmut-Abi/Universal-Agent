# Evaluation datasets & suites

Two different registries live in this directory — they are not interchangeable:

| Artifact | Format | Discovered by |
| --- | --- | --- |
| **Scenario suite** | `{"name", "scenarios": [...], "tags", "quality_gate"}` (see `cross_domain_scenarios.json`) | `agent eval run --suite-file <file>` |
| **Dataset manifest** | `dataset.json` (below) referencing one or more suite files | `agent eval datasets --dataset-dir <dir>` |

## Dataset manifest format (`dataset.json`)

```json
{
  "apiVersion": "agent.nantian.dev/v1alpha1",
  "kind": "EvaluationDataset",
  "metadata": {"name": "...", "version": "0.1.0", "description": "...", "tags": []},
  "domains": [{"name": "kubernetes", "version": "0.2.0"}],
  "suites": [{"name": "...", "path": "cross_domain_scenarios.json", "tags": []}]
}
```

- `suites[].path` is relative to this directory and must parse as a scenario suite.
- `eval datasets --dataset-dir examples/evaluation --verify` validates the manifest
  and every referenced suite file.

## Commands

```bash
agent eval datasets --dataset-dir examples/evaluation            # list
agent eval datasets --dataset-dir examples/evaluation --verify   # verify
agent eval run <profile> --suite-file cross_domain_scenarios.json  # run one suite
```
