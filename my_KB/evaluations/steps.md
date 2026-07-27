Here's a complete map of scripts by layer with exact execution steps.

Layer 1 — Live Workflow (Golden Test Cases)
Scripts:


agent-mesh/workflow_evaluations/workflow/
├── dataset_builder.py    ← defines the 20 hand-crafted golden test cases (Groups A–E)
├── run_maf_eval.py       ← sends those cases to live agents, captures results
└── results_reporter.py   ← formats results into table / JSON / CSV
What it tests: Your actual running agents — real requests, real responses, checked against known-correct answers.

How to run: (mesh must be running first)


# Step 1 — start the mesh in a separate terminal
cd agent-mesh
python launch_mesh.py
# Wait until all 4 agents show "ready"

# Step 2 — run Layer 1 as part of full eval (from agent-mesh/ directory)
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode full
There is no standalone --mode for just Layer 1. It runs as part of --mode full.

Layer 2 — Custom Evaluators (FAB-specific safety checks)
Scripts:


agent-mesh/workflow_evaluations/evaluators/
├── pii_evaluator.py           ← checks if agent leaked phone/IBAN/NationalID in response
├── compliance_evaluator.py    ← checks if agent made correct allow/block/bypass decision
├── rbac_evaluator.py          ← checks if agent respected data-access boundaries by user role
├── rag_citation_evaluator.py  ← checks if RAGAgent cited a named source document
└── data_tool_evaluator.py     ← checks if DataAgent called the correct SQL view
What it tests: Safety and correctness rules specific to your banking use case — no live agents needed.

How to run:


# From agent-mesh/ directory — works WITHOUT mesh running
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode ci
Expected output:


=== CI / Evaluator Smoke Tests ===
[OK] pii_evaluator: +971 phone blocked → score=0.000 (expected 0.0)
[OK] compliance_evaluator: legit query → score=1.0 correct decision
[OK] rbac_evaluator: dave accessing CUST_009 → RBAC_VIOLATION
...
Layer 3 — Financial Benchmarks (36 public NLP datasets)
Scripts:


agent-mesh/workflow_evaluations/financial_benchmarks/
├── task_registry.py      ← master list of all 36 tasks + which agent handles each
├── demo_runner.py        ← verbose per-sample runner (the one you want to use)
├── flare_runner.py       ← legacy FLARE-specific runner
├── finben_runner.py      ← legacy FinBEN-specific runner
└── benchmark_report.py   ← aggregates all 3 layers into final report files
What it tests: Whether the LLMs understand financial language — using standardised public datasets with pre-verified gold answers.

How to run — 3 options:


# Option A: Tier 1 only (18 tasks, no login needed) — RECOMMENDED STARTING POINT
# Mesh must be running
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode demo --tier 1

# Option B: All 36 tasks (needs HuggingFace login for gated datasets)
huggingface-cli login          # one-time setup, paste your HF token
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode demo --tier 2

# Option C: Dry-run (no mesh needed — just verifies datasets load correctly)
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode demo --dry-run
Test a single task (spot check):


cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode single --agent rag --task flare_ner
python workflow_evaluations/run_evaluation.py --mode single --agent data --task flare_bigdata22
python workflow_evaluations/run_evaluation.py --mode single --agent compliance --task flare_headlines
python workflow_evaluations/run_evaluation.py --mode single --agent price_assist --task finben_finqa
Run Everything at Once

# Runs Layer 2 (ci) + Layer 1 (live workflow) + Layer 3 (benchmarks) sequentially
# Requires: mesh running + huggingface-cli login
cd agent-mesh
python workflow_evaluations/run_evaluation.py --mode full
Quick Reference
Layer	Mode flag	Needs mesh?	Needs HF login?
Layer 2 only	--mode ci	No	No
Layer 3 demo (Tier 1)	--mode demo --tier 1	Yes	No
Layer 3 demo (Tier 2, all 36)	--mode demo --tier 2	Yes	Yes
Layer 3 spot check	--mode single --agent X --task Y	Yes	No
All layers	--mode full	Yes	Yes (for complete coverage)
Reports are saved to agent-mesh/workflow_evaluations/reports/ with timestamps.