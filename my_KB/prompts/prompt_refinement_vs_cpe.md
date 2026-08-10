# Prompt Refinement vs. Conversational Prompt Engineering (CPE)
# Analysis for agent-mesh Banking System

## The Two Techniques

| | Prompt Refinement (PR) | Conversational Prompt Engineering (CPE) |
|---|---|---|
| **Loop type** | Offline | Online |
| **Trigger** | Accumulated failure signals (feedback, eval scores) | Active conversation dynamics (corrections, clarifications) |
| **Output** | Edited prompt files with verified quality improvement | Per-session prompt adaptation (ephemeral) |
| **Auditability** | Full — git diff + eval score + CI gate per change | None — corrections are session-scoped |

---

## What Already Exists in This Codebase

### For Prompt Refinement (80% ready)

| Signal | File | What it contains |
|---|---|---|
| 18-dimension eval scores | `workflow_evaluations/reports/*.json` | Score per dimension per golden case |
| Human thumbs up/down | `data/feedback.jsonl` | Query, answer, rating, fine_tune_record |
| 7-dimension structured feedback | `data/feedback.jsonl` (structured records) | Per-phase error codes + correction_text |
| LLM reasoning per turn | `data/conversations/*/assistant.reasoning[]` | Intent, confidence, routing decisions |
| CI regression gate | `workflow_evaluations/ci_gate.py` | Blocks merges if 7 gate metrics regress |

**Only missing:** A script that reads these signals and proposes prompt patches.

### For CPE (30% ready)

| Signal | File | Status |
|---|---|---|
| Multi-turn conversation history | `data/conversations/*.jsonl` | Captured but not used for real-time adaptation |
| Rolling summary | Same file | Already injected into next-turn prompt (basic CPE) |
| Correction text | `data/feedback.jsonl` (dimensions.correction.correction_text) | Captured but not fed back into session |

**Missing:** Correction detection within sessions + session_corrections state + injection into prompt.

---

## The 7-Dimension Feedback → Prompt Section Mapping

This is the key insight that makes Prompt Refinement precise in this codebase:

| Feedback dimension | Error codes | Responsible prompt section |
|---|---|---|
| `intent` | wrong_intent, missing_context | PriceAssistAgent: intent classification section |
| `tools` | wrong_tool, evidence_missing | PriceAssistAgent: tool routing + sub-agent prompts |
| `policy` | policy_check_missed, wrong_risk_class | ComplianceAgent: COMPLIANCE_INSTRUCTIONS |
| `output` | incomplete, not_grounded, not_actionable | PriceAssistAgent: operating rules (rules 3–9) |
| `workflow` | steps_out_of_order, check_skipped | workflow.py executor ordering (structural) |
| `correction` | + correction_text field | Any agent — correction_text = ground-truth example |
| `effort` | user_escalated, user_abandoned | Severity signal — prioritize these failures |

---

## Prompt Refinement Architecture

```
data/feedback.jsonl (down-rated + structured)
+ workflow_evaluations/reports/*.json (failing eval cases)
    ↓
PromptRefinementAnalyzer (new: src/prompts/refinement_analyzer.py)
  - Groups failures by dimension code + frequency
  - Maps code → responsible prompt section
  - Extracts correction_text examples
    ↓
PatchGenerator (LLM call, same httpx pattern as cache_judge.py)
  - Input: prompt section + failures + correction examples
  - Output: proposed edit to that section
    ↓
Evaluation verification (existing: workflow_evaluations/run_evaluation.py)
  - Must improve targeted dimension
  - Must not regress any CI gate metric
    ↓
Apply or discard
```

### New Files

| File | Purpose |
|---|---|
| `src/prompts/refinement_analyzer.py` | Reads signals, groups by dimension, identifies sections |
| `scripts/refine_prompts.py` | CLI: `--analyze`, `--propose`, `--apply` |

### Prompt Section Tagging (enables surgical patches)

Add section markers to prompt constants:
```python
PRICE_ASSIST_INSTRUCTIONS = """
<!-- section: intent_classification -->
...
<!-- /section -->
<!-- section: operating_rules -->
...
<!-- /section -->
"""
```
Analyzer extracts and replaces individual sections without touching the rest.

---

## CPE Architecture (Session-Level)

```
Turn N: "What is CUST001's margin?"  → answer: gross margin
Turn N+1: "No, I meant net margin"
    ↓
CorrectionDetector (new: src/prompts/correction_detector.py)
  - Fast LLM call: "Is this query correcting the previous exchange?"
  - Output: "Focus on NET margin, not gross" or None
    ↓
state.session_corrections.append("Focus on NET margin, not gross")
    ↓
Turn N+2 user message:
  <session_corrections>
  - Focus on NET margin, not gross
  </session_corrections>
  [User context]
  {query}
```

### New Code

| File | Purpose |
|---|---|
| `src/prompts/correction_detector.py` | Detects corrections, returns one-line note or None |
| `MeshState.session_corrections: List[str]` | Accumulated correction notes for this session |
| Orchestrator update | Calls detector before workflow when history ≥ 2 turns |
| DomainExecutor update | Injects `<session_corrections>` block if non-empty |

---

## Recommendation: Prompt Refinement First, CPE as Complement

### Why Prompt Refinement is the Better Fit

1. **Infrastructure is 80% built.** `data/feedback.jsonl` with 7-dimension codes + `correction_text` + evaluation suite + CI gate — all exist. Only the analyzer + CLI script are new.

2. **Banking demands auditability.** Every prompt change via Prompt Refinement has: git diff, evaluation score comparison, CI gate result. CPE's session corrections are ephemeral and unaudited.

3. **Dimension codes give surgical precision.** `wrong_risk_class` → ComplianceAgent. `evidence_missing` → tool routing. No guessing which prompt section to fix.

4. **CI gate prevents regression.** Every patch is tested before deployment. Safe to eventually automate.

5. **CPE adds latency.** Correction detection = +1 LLM call per turn. Only pays off in multi-turn sessions with corrections — a minority of banking queries.

### Where CPE Adds Value

- After Prompt Refinement reduces systematic failures, CPE Mode A (session corrections) handles the residual case: one-off per-session misunderstandings
- The `correction_text` from structured feedback becomes the bridge: feedback corrections → Prompt Refinement training signal AND → synthetic CPE test cases
- Fix the HITL persistence gap first (`data/feedback.jsonl`, `record_type: "hitl_decision"`) — HITL approve/reject decisions on compliance edge cases are currently lost (in-memory only)

---

## Implementation Sequence

1. **PR Phase 1**: `refinement_analyzer.py` + `scripts/refine_prompts.py` — reads signals, proposes patches
2. **PR Phase 2**: Prompt section tagging in `*_agent.py` files — enables surgical patches
3. **PR Phase 3**: CI gate integration in `--apply` mode — automated safety gate
4. **CPE Phase 1**: Fix HITL persistence gap — `data/feedback.jsonl` with `record_type: "hitl_decision"`
5. **CPE Phase 2**: `correction_detector.py` + `MeshState.session_corrections` + orchestrator/DomainExecutor updates
