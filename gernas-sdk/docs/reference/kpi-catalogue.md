# GERNAS KPI catalogue: source signals and formulas (Blueprint §4, §5, §6, §11)

Blueprint §4 asks, for each KPI:

> Definition → Formula → Source → Owner → Threshold → SLO → Dashboard → Alert → Retention → Business interpretation

This catalogue fixes the **definition, formula and source signal**, which come from the Gernas SDK telemetry model.

**Owner, threshold, SLO, alert and retention are FAB decisions.** They are marked *TBD (FAB)* and tracked as open items in `checklist.md`.

**Formulas are draft KQL over the Collector's Azure Data Explorer tables** (`OTELTraces`, `OTELMetrics`). They have **not been executed**, because no ADX cluster is available yet; validate them when ADX is provisioned.

Notation:
- `T` = `OTELTraces`; attributes are read as `tostring(TraceAttributes["gernas.<field>"])`.
- `M` = `OTELMetrics` with `MetricName`.
- "Dashboard" refers to the blueprint §10 numbering.

| # | KPI (blueprint) | Definition | Source signal (Gernas) | Formula (draft KQL) | Dashboard |
|---|---|---|---|---|---|
| 4A-1 | Active AI users | Unique initiating users | `tokenized_user_id` on spans (needs `GERNAS_USER_TOKEN_KEY`) | `T \| summarize dcount(tostring(TraceAttributes["gernas.tokenized_user_id"]))` | 1 |
| 4A-2 | Active use cases | Use cases with activity | `use_case.request` spans, `gernas.use_case.requests` | `T \| where SpanName=="use_case.request" \| summarize dcount(tostring(TraceAttributes["gernas.use_case_id"]))` | 1 |
| 4A-3 | Active agents | Agents executing in the period | `agent.run` (`agent_id`) | `T \| where SpanName=="agent.run" \| summarize dcount(tostring(TraceAttributes["gernas.agent_id"]))` | 1, 3 |
| 4A-4 | Agent executions | Total agent runs | `gernas.agent.runs` | `M \| where MetricName=="gernas.agent.runs" \| summarize sum(MetricValue)` | 1, 3 |
| 4A-5 | Successful outcomes | Business tasks completed successfully | `business_outcome` on `use_case.request`; `gernas.use_case.requests` | `T \| where SpanName=="use_case.request" \| summarize countif(tostring(TraceAttributes["gernas.business_outcome"])=="success")` | 1 |
| 4A-6 | Adoption trend | WoW / MoM volume | as 4A-2 / 4A-4 | `… \| summarize count() by bin(StartTime, 7d)` | 1 |
| 4A-7 | Sessions / user | Usage intensity | `session_id`, `tokenized_user_id` | `dcount(session_id) / dcount(tokenized_user_id)` | 1 |
| 5-1 | Agent task success rate | % runs completing the intended task | `termination_reason=completed` on `agent.run`; `business_outcome` | `countif(reason=="completed") / count()` over `agent.run` | 3 |
| 5-2 | Agent run latency | End-to-end run duration | `gernas.agent.run.duration` (also span duration) | `percentiles(EndTime-StartTime, 50, 95)` over `agent.run` | 3 |
| 5-3 | Planning latency | Time spent generating the plan | `gernas.agent.plan.duration`; `agent.plan` spans | `percentiles(...)` over `agent.plan` | 3 |
| 5-4 | Average steps / run | Execution complexity | `gernas.agent.steps`; `step_count` on `agent.run` | `avg(toint(TraceAttributes["gernas.step_count"]))` | 3 |
| 5-5 | Tool calls / run | Dependency consumption | `tool.call` spans with `agent_run_id` | `count(tool.call) / dcount(agent_run_id)` | 3 |
| 5-6 | Failed tool calls | Tool reliability | `gernas.tool.calls{execution_status=error}` | `countif(execution_status=="error")` over `tool.call` | 3 |
| 5-7 | Retry rate | Execution inefficiency | `gernas.agent.retries`; `retry_count` | `sum(retries) / sum(runs)` | 3 |
| 5-8 | Agent loop rate | Repetitive execution | `gernas.agent.loops`; `loop_detected` | `countif(loop_detected) / count()` over `agent.run` | 3 |
| 5-9 | Maximum step violation | Potential runaway agent | `termination_reason=max_steps`; `max_step_violation` | `countif(reason=="max_steps")` | 3 |
| 5-10 | Delegation count | Multi-agent complexity | `gernas.agent.delegations`; `agent.delegate` spans | `count()` over `agent.delegate` | 3 |
| 5-11 | Agent hand-off latency | Multi-agent overhead | `gernas.agent.delegation.duration` | `percentiles(duration, 95)` over `agent.delegate` | 3 |
| 5-12 | Human intervention rate | HITL requirement | `gernas.hitl.interventions` / `gernas.agent.runs` | ratio | 3 |
| 5-13 | Abandonment rate | Runs terminated before completion | `termination_reason=abandoned` | `countif(reason=="abandoned") / count()` | 3 |
| 5-14 | Timeout rate | Runs exceeding the execution window | `termination_reason=timeout` | as above | 3 |
| 5-15 | Fallback rate | Alternate model or tool required | `gernas.fallbacks`; `fallback_used` | `sum(fallbacks) / sum(llm.calls)` | 3, 4 |
| 5-16 | Policy block rate | Governance controls triggered | `gernas.policy.decisions{decision=deny}`; `termination_reason=policy_block` | `countif(decision=="deny") / count()` | 3, 6 |
| 5-17 | Cost per successful agent task | AED per successful business outcome | `estimated_cost` (LLM + embedding) joined to `business_outcome` by `trace_id` | `sum(cost) / countif(outcome=="success")`. **Partial**: §11 non-LLM cost components are platform-metered | 7 |
| 6-1 | Request volume | Model calls | `gernas.llm.calls` | `sum(MetricValue)` | 4 |
| 6-2 | TTFT | First-token latency | `gernas.llm.ttft`; `ttft_ms` | `percentiles(...)` | 4 |
| 6-3 | Total generation latency | Model call duration | `gernas.llm.duration` | `percentiles(...)` | 4 |
| 6-4 | Tokens per second | Throughput | `gernas.llm.tokens_per_second` | `avg(...)` | 4 |
| 6-5 / 6-6 | Input / output tokens | Consumption | `gernas.llm.tokens{token_type}` | `sum by token_type` | 4, 7 |
| 6-7 | Context size | Prompt size | — | **Not captured**: no blueprint field for context size; `token_input` is the proxy | 4 |
| 6-8 | Cache usage | Cached input tokens | `gernas.llm.tokens{token_type=cached}`; `token_cached` | `sum` | 4 |
| 6-9 / 6-10 / 6-11 | Timeout / rate limit / retry | Model errors | `gernas.llm.calls{error_category=timeout\|rate_limit}`; `gernas.agent.retries` | `countif(...)` | 4 |
| 6-12 | Model fallback | Fallbacks | `gernas.fallbacks{operation=llm.call}` | `sum` | 4 |
| 6-13 | PTU utilization | Provisioned throughput use | — | **Azure OpenAI / APIM metric**, not an agent SDK signal | 8 |
| 6-Q | Quality: groundedness, relevance, faithfulness, answer completeness, citation quality, hallucination, toxicity / safety, refusal rate | Evaluation scores (where evaluation is enabled) | `evaluation.score` spans + `gernas.evaluation.score{evaluation_metric}` via `gernas.record_evaluation` | `avg(score) by metric`; refusal rate = `avg(score)` of `refusal` | 4, 5 |
| 6-C | Consumption attribution | Use case → agent → run → user → model → cost | Envelope + run context + `tokenized_user_id` + `model_id` + `estimated_cost` on every `llm.call` | `T \| where SpanName=="llm.call" \| summarize sum(cost) by use_case_id, agent_id, agent_run_id, tokenized_user_id, model_id` | 7 |
| 3-D1 | Documents received / parsed / rejected | Document-processing volume and outcome (§3) | `gernas.documents{document_type, document_status}`; `document.process` spans | `M \| where MetricName=="gernas.documents" \| summarize sum(MetricValue) by tostring(MetricAttributes["gernas.document_status"])` | 3 |
| 3-D2 | Parsing failure rate | Documents that failed processing | `document_status=failed` | `countif(status=="failed") / count()` over `document.process` | 3 |
| 3-D3 | OCR / extraction latency | Extraction step duration | `agent.step` with `step_type=document_extraction` | `percentiles(EndTime-StartTime, 50, 95)` | 3 |
| 3-D4 | Chunks per document | Chunking output | `gernas.document.chunks`; `chunk_count` | `avg(...)` by `document_type` | 3 |

Columns not covered above (Owner, Threshold, SLO, Alert, Retention, Business interpretation): **TBD (FAB)** for every KPI. Alert rules and dashboards-as-code are planned in WP3 (`md_files/IMPLEMENTATION_PLAN_WP2.md`).
