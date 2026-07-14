# FAB AgentMesh — Evaluation Suite

Three-layer evaluation framework for the FAB AgentMesh pricing assistant.

## Layers

| Layer | What it tests | Requires live agents? |
|---|---|---|
| **Layer 1 — Workflow eval** | End-to-end golden test cases (Groups A–E) | Yes (live) or No (log replay) |
| **Layer 2 — Custom evaluators** | Compliance, PII, RBAC, RAG citation, tool routing | No |
| **Layer 3 — Financial benchmarks** | FLARE + FinBEN public datasets | Yes (live agents + HF auth) |

## Quick start

```bash
# CI mode — evaluator smoke tests + threshold checks (no live agents needed)
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode ci

# Replay audit log
python workflow_evaluations/run_evaluation.py --mode replay --log data/audit_trail.jsonl

# Full run (all 3 layers, agents must be running)
python workflow_evaluations/run_evaluation.py --mode full

# Benchmarks dry-run (no LLM calls — just prints dataset sizes)
python workflow_evaluations/run_evaluation.py --mode benchmarks --dry-run

# Single benchmark task
python workflow_evaluations/run_evaluation.py --mode single --agent api --task finben_ectsum
```

## File structure

```
workflow_evaluations/
  config.py                          # endpoints, sample sizes, pass thresholds
  run_evaluation.py                  # CLI entry point
  requirements_eval.txt              # rouge-score

  evaluators/
    compliance_evaluator.py          # compliance_decision_correct, prompt_injection_blocked
    pii_evaluator.py                 # pii_not_in_response, redaction_tokens_present
    rbac_evaluator.py                # rbac_scope_respected
    rag_citation_evaluator.py        # citation_present_and_valid, rag_answer_not_hallucinated
    data_tool_evaluator.py           # correct_sql_view_called, data/rag_agent_was_called

  workflow/
    dataset_builder.py               # GoldenTestCase + Groups A–E (20 cases)
    run_maf_eval.py                  # live + replay evaluation runners
    results_reporter.py              # console table + JSON + CSV output

  financial_benchmarks/
    flare_runner.py                  # FPB, FinQA, ConvFinQA, BigData22
    finben_runner.py                 # NER, FiQA, ECTSum, Headlines, FinQA
    benchmark_report.py              # BenchmarkReport + JSON/Markdown/CSV output
    datasets/                        # local dataset cache (gitignored)

  reports/                           # evaluation output (gitignored)
```

## Pass thresholds

| Metric | Threshold |
|---|---|
| Compliance decision correct | >= 0.95 |
| PII not in response | = 1.00 |
| RBAC scope respected | = 1.00 |
| Citation present rate | >= 0.80 |
| Tool call accuracy | >= 0.85 |
| FLARE FPB F1 | >= 0.70 |
| FinBEN ECTSum ROUGE-1 | >= 0.35 |

## Golden test case groups

- **Group A** (4 cases): Structured data queries → DataAgent only
- **Group B** (4 cases): Policy/knowledge queries → RAGAgent only
- **Group C** (3 cases): Hybrid queries → DataAgent + RAGAgent
- **Group D** (3 cases): Security scenarios (prompt injection, SQL injection, RBAC)
- **Group E** (6 turns across 2 conversations): Multi-turn memory tests
