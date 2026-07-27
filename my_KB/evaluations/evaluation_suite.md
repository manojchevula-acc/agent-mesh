# Evaluation Suite

AgentMesh has a comprehensive evaluation framework in `workflow_evaluations/` covering functional correctness, security, tool use, and financial benchmarks.

---

## Running Evaluations

**Entry point:** `workflow_evaluations/run_evaluation.py`

```bash
python workflow_evaluations/run_evaluation.py --mode <mode>
```

| Mode | What runs |
|---|---|
| `ci` | Smoke-test subset — fast, used in CI gate |
| `full` | All 15 evaluators over the full dataset |
| `benchmarks` | FinBen + FLARE financial benchmark runners |
| `replay` | Replay from `data/audit_trail.jsonl` |
| `single` | Single agent/task evaluation |
| `demo` | Tier 1 (public datasets) or Tier 2 (all 36 tasks) |
| `redteam` | Adversarial red-team attack suite |

---

## The 15 Evaluators

**Directory:** `workflow_evaluations/evaluators/`

### Security & Access
| Evaluator | File | What it checks |
|---|---|---|
| Compliance | `compliance_evaluator.py` | ComplianceAgent correctly blocks/passes known-bad/known-good inputs |
| RBAC | `rbac_evaluator.py` | Role-based access controls correctly allow/deny by role |
| PII | `pii_evaluator.py` | No PII leaks in final answers |

### Response Quality
| Evaluator | File | What it checks |
|---|---|---|
| Task Completion | `task_completion_evaluator.py` | Answer actually addresses the query |
| Task Adherence | `task_adherence_evaluator.py` | Answer stays within RBAC scope |
| Keyword Coverage | `keyword_coverage_evaluator.py` | Expected keywords present in answer |
| Intent Resolution | `intent_resolution_evaluator.py` | Correct data/knowledge/hybrid routing |
| Ambiguity Resolution | `ambiguity_resolution_evaluator.py` | Ambiguous queries resolved correctly |

### Tool Use
| Evaluator | File | What it checks |
|---|---|---|
| Tool Selection | `tool_selection_evaluator.py` | Right tool chosen for the query type |
| Tool Call Success | `tool_call_success_evaluator.py` | Tool calls completed without error |
| Tool Input Accuracy | `tool_input_accuracy_evaluator.py` | Correct parameters passed to tools |
| Tool Output Utilization | `tool_output_utilization_evaluator.py` | Tool results actually used in answer |
| Data Tool | `data_tool_evaluator.py` | DataLayer tools used correctly |

### RAG Quality
| Evaluator | File | What it checks |
|---|---|---|
| RAG Citation | `rag_citation_evaluator.py` | Policy answers include correct source citations |

### LLM-as-Judge
| Evaluator | File | What it checks |
|---|---|---|
| LLM Evaluators | `llm_evaluators.py` | 6 dimensions via LLM judge: task_adherence, completeness, tool_appropriateness, rag_faithfulness, citation_accuracy, data_accuracy |

The LLM-as-judge suite batches **2 API calls per test case** to evaluate all 6 dimensions efficiently.

---

## Financial Benchmarks

**Directory:** `workflow_evaluations/financial_benchmarks/`

| File | Description |
|---|---|
| `finben_runner.py` | FinBen financial NLP benchmark runner |
| `flare_runner.py` | FLARE benchmark runner |
| `task_registry.py` | Task definitions for both benchmarks |
| `benchmark_report.py` | Full benchmark report generation |
| `single_reporter.py` | Single-task report |

See [research/FinBEN_FLARE.md](../research/FinBEN_FLARE.md) for benchmark reference details.

---

## Red-Team Suite

**Directory:** `workflow_evaluations/red_team/`

### Attack Categories (`red_team_taxonomy.py`)

`RED_TEAM_CATEGORIES` defines structured adversarial prompts across 6 attack categories:

1. **Prompt injection** — attempts to override system instructions
2. **PII exfiltration** — requests to reveal/export personal data
3. **Role escalation** — attempts to claim a higher-privilege role
4. **Jailbreak** — creative attempts to bypass safety guardrails
5. **Social engineering** — manipulation to grant unauthorized access
6. **Data poisoning** — injecting false context into conversation

### Runner (`red_team_runner.py`)

Sends adversarial prompts to the live running mesh, then:
- Verifies each attack was **blocked** (compliance failed or guardrail triggered)
- Checks for any **PII leakage** in responses to blocked requests
- Writes a JSON report with pass/fail per attack and overall block rate

---

## CI Gate

**File:** `workflow_evaluations/ci_gate.py`

Pass/fail thresholds for automated pipeline gating. If any evaluator falls below its threshold, the CI job fails.

---

## Workflow Tooling

| File | Purpose |
|---|---|
| `workflow/dataset_builder.py` | Build evaluation datasets |
| `workflow/results_reporter.py` | Generate evaluation reports |
| `workflow/ci_reporter.py` | Format results for CI output |
| `workflow/grafana_push.py` | Push evaluation metrics to Grafana dashboard |
| `workflow/run_maf_eval.py` | MAF-specific evaluation runner |
