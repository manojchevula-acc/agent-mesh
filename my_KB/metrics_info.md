# AgentMesh — Metrics Reference

**Source:** `agent-mesh/src/observability/metrics.py`  
**OTel Meter:** `agent_mesh` v1.0.0  
**Gate:** All metrics require `Config.ENABLE_BUSINESS_METRICS = True`; all `record_*` wrappers are crash-safe (`try/except pass`).

> **Are these real?** Yes — all 26 metrics (13 counters + 13 histograms) are fully implemented and actively recorded in the pipeline. Nothing is documentation-only.

---

## Are Any of These FAB Business-Specific?

**Short answer: No.** All 26 current metrics are platform/infrastructure metrics. None of them capture a FAB banking business outcome (product type queried, pricing compliance result, credit rating tier, customer segment, regulatory category, etc.).

They fall into two groups:

### Group A — Pure Infrastructure (would be identical in any agent mesh, any industry)
`fab.a2a.calls.total`, `fab.a2a.duration`, `fab.mcp.calls.total`, `fab.mesh.requests.total`, `fab.mesh.request.duration`, `fab.domain.duration`, `fab.conversation.*`, `fab.llm.*` (all token + cost metrics)

### Group B — Platform-specific but NOT banking-domain
These carry FAB platform labels (role names, route names) but don't capture banking outcomes:

| Metric | What it captures | What's missing |
|--------|-----------------|----------------|
| `fab.rbac.requests.total` | PASS/BLOCK + FAB role name | No product, no query type, no business outcome |
| `fab.compliance.requests.total` | PASSED/FAILED/BYPASSED + role | No context about what was reviewed |
| `fab.guardrail.requests.total` | PASS/BLOCK + violation category | Category is technical (pii, injection), not banking domain |
| `fab.domain.route.total` | Data / RAG / Hybrid route | Route is infra, not product or intent |
| `fab.output.redaction.pii_hits` | Count of redacted tokens | No role or agent context |

**See the "Proposed FAB Business Metrics" section at the bottom for what should be created.**

---

## Counters

Prometheus note: OTel exports counters with dots replaced by underscores (e.g. `fab.guardrail.requests.total` → `fab_guardrail_requests_total`).

| # | OTel Metric Name | Unit | Attributes / Labels | Description | Where Recorded | Prometheus Query (rate) | Calculation | Real-World Value |
|---|-----------------|------|---------------------|-------------|----------------|------------------------|-------------|-----------------|
| 1 | `fab.guardrail.requests.total` | `{request}` | `result` (PASS/BLOCK), `category` (violation type or "none"), `stage` ("input_guardrail") | Total input guardrail evaluations | `workflow.py` → `InputGuardrailExecutor.run()` lines 242, 264 | `sum by (result)(rate(fab_guardrail_requests_total[5m]))` | +1 per guardrail eval via `record_guardrail(result, category, duration_ms)` | **Security watchdog**: tells you if someone is trying to inject malicious prompts or sneak PII into requests. A spike in BLOCK means an attack or misuse is happening right now. |
| 2 | `fab.rbac.requests.total` | `{request}` | `result` (PASS/BLOCK), `role` (user's banking role) | Total RBAC role validation checks | `workflow.py` → `RBACValidationExecutor.run()` lines 318, 338 | `sum by (result)(rate(fab_rbac_requests_total[5m]))` | +1 per RBAC check via `record_rbac(result, role, duration_ms)` | **Access control audit**: shows which banking roles are being rejected. If `credit_officer` is getting BLOCKed repeatedly, it flags a misconfigured role or an impersonation attempt. |
| 3 | `fab.compliance.requests.total` | `{request}` | `result` (PASSED/FAILED/BYPASSED), `role` | Total compliance A2A reviews | `workflow.py` → `ComplianceExecutor.run()` lines 384, 425, 449 | `sum by (result)(rate(fab_compliance_requests_total[5m]))` | +1 per compliance review via `record_compliance(result, role, duration_ms)` | **Compliance gate health**: if FAILED rate rises, the AI is detecting more suspicious requests — potential breach attempt or a prompt template problem. BYPASSED count shows how often elevated roles skip the gate. |
| 4 | `fab.mesh.requests.total` | `{request}` | `result` (SUCCESS/BLOCKED/ERROR), `block_stage` (stage name or "none") | Total end-to-end mesh requests | `orchestrator.py` → `handle_request()` lines 169–173 | `sum by (result)(rate(fab_mesh_requests_total[5m]))` | +1 per full request via `record_mesh_request(result, block_stage, duration_ms)` | **System health at a glance**: the single most important counter — shows overall traffic volume, success rate, and which pipeline stage is causing blocks/failures. Your homepage Grafana panel. |
| 5 | `fab.domain.route.total` | `{request}` | `route` ("Data Layer Service" / "RAG Service" / "Data Layer + RAG (Hybrid)") | Routing decisions by route type | `workflow.py` → `DomainExecutor.run()` line 710 | `sum by (route)(rate(fab_domain_route_total[5m]))` | +1 per routing decision via `record_domain_route(route, duration_ms)` | **Workload split**: tells you if users are mostly asking factual data questions (Data Layer) vs policy/procedure questions (RAG) vs complex pricing queries (Hybrid). Informs which backend to scale. |
| 6 | `fab.a2a.calls.total` | `{request}` | `target_node` (agent name), `result` (SUCCESS/ERROR) | Total A2A hops by target node | `workflow.py` lines 709, 712 and `a2a/clients.py` `ask_remote()` | `sum by (target_node, result)(rate(fab_a2a_calls_total[5m]))` | +1 per A2A hop via `record_a2a_call(target_node, result, duration_ms)`; recorded in both DomainExecutor and ask_remote() | **Agent dependency health**: if `price_assist` ERROR rate spikes, the pricing agent is down and users are getting degraded answers. Immediately tells you which downstream agent is the problem. |
| 7 | `fab.mcp.calls.total` | `{request}` | `service` ("datalayer"/"rag"), `tool_name`, `result` (SUCCESS/ERROR) | Total MCP tool invocations | `middleware/audit_middleware.py` `AuditMiddleware.process()` lines 206–210 | `sum by (service, result)(rate(fab_mcp_calls_total[5m]))` | +1 per MCP call via `record_mcp_call(service, tool_name, result)`; only for DataAgent and RAGAgent | **Backend service health**: shows if the DataLayer or RAG services are throwing errors. A sudden ERROR surge means the SQL views or vector store is having issues, even before users complain. |
| 8 | `fab.conversation.load.total` | `{request}` | `result` ("ok"/"empty"/"error"), `backend` ("jsonl"/"redis") | Total conversation history load operations | `orchestrator.py` lines 120, 128 | `sum by (result)(rate(fab_conversation_load_total[5m]))` | +1 per load via `record_conversation_load(result, backend, duration_ms, turns)` | **Memory system reliability**: "empty" means new sessions, "ok" means returning users, "error" means the conversation store is failing. If error rate climbs, users lose their conversation context — a bad experience. |
| 9 | `fab.conversation.append.total` | `{request}` | `result` ("ok"/"error"), `backend` ("jsonl"/"redis") | Total conversation turn append operations | `orchestrator.py` lines 190, 194 | `sum by (result)(rate(fab_conversation_append_total[5m]))` | +1 per append via `record_conversation_append(result, backend, duration_ms)` | **Conversation persistence health**: errors here mean turns are being lost — the next time a user asks a follow-up question, the AI won't remember the previous context. Silent data loss indicator. |
| 10 | `fab.llm.tokens.input` | `{token}` | `agent` (agent name), `model` (model id), `role` (user role) | LLM prompt tokens consumed | `a2a/clients.py` line 114; `middleware/audit_middleware.py` line 249 | `sum by (agent, model)(rate(fab_llm_tokens_input_total[5m]))` | `+= input_tokens` per LLM call via `record_llm_tokens()`; exact from usage object or `chars // 4` estimate | **Prompt bloat detector**: large input token counts mean prompts (including injected conversation history) are growing too big. Helps catch runaway context windows before they hit model limits or inflate costs. |
| 11 | `fab.llm.tokens.output` | `{token}` | `agent`, `model`, `role` | LLM completion tokens generated | Same as above | `sum by (agent, model)(rate(fab_llm_tokens_output_total[5m]))` | `+= output_tokens` per LLM call via `record_llm_tokens()` | **Response verbosity tracker**: spikes mean the model is generating very long answers. Useful for tuning max_tokens limits and catching models that are over-explaining. |
| 12 | `fab.llm.tokens.total` | `{token}` | `agent`, `model`, `role` | LLM total tokens (input + output) | Same as above | `sum by (agent, model)(rate(fab_llm_tokens_total_total[5m]))` | `+= input_tokens + output_tokens` per call via `record_llm_tokens()` | **Cost and capacity planning**: total token throughput is the single number that drives your Groq API bill and tells you when to upgrade your rate limit tier. |

---

## Histograms

Prometheus exports histograms as `_bucket`, `_sum`, and `_count` series. Use `histogram_quantile()` for percentile queries.

| # | OTel Metric Name | Unit | Attributes / Labels | Description | Where Recorded | Prometheus Query (p95) | Calculation | Real-World Value |
|---|-----------------|------|---------------------|-------------|----------------|------------------------|-------------|-----------------|
| 1 | `fab.guardrail.duration` | ms | `result` (PASS/BLOCK) | Guardrail wall-clock time | `workflow.py` `InputGuardrailExecutor.run()` | `histogram_quantile(0.95, sum by (le)(rate(fab_guardrail_duration_bucket[5m])))` | `time.perf_counter()` delta from executor entry to guardrail response, converted to ms | **Security pipeline latency**: if the guardrail is taking >500ms, it's adding half a second to every single user request. Helps decide if the regex/LLM safety check needs optimization. |
| 2 | `fab.rbac.duration` | ms | `result` (PASS/BLOCK) | RBAC validation wall-clock time | `workflow.py` `RBACValidationExecutor.run()` | `histogram_quantile(0.95, sum by (le)(rate(fab_rbac_duration_bucket[5m])))` | `time.perf_counter()` delta for RBAC check, converted to ms | **Access check speed**: should be near-zero (in-memory set lookup). If it suddenly spikes, something upstream changed (e.g. role lookup hit a network call). Fast detection of configuration drift. |
| 3 | `fab.compliance.duration` | ms | `result` (PASSED/FAILED/BYPASSED) | Compliance A2A review wall-clock time | `workflow.py` `ComplianceExecutor.run()` | `histogram_quantile(0.95, sum by (le)(rate(fab_compliance_duration_bucket[5m])))` | `time.perf_counter()` delta including A2A round-trip to compliance agent | **Compliance agent SLA**: this is an LLM-powered A2A call and typically the slowest gate. If p95 exceeds your SLA target, you'll know before users start complaining about slow responses. |
| 4 | `fab.domain.duration` | ms | `route` | Domain dispatch wall-clock time | `workflow.py` `DomainExecutor.run()` line 710 | `histogram_quantile(0.95, sum by (le, route)(rate(fab_domain_duration_bucket[5m])))` | `time.perf_counter()` delta of price_assist A2A call | **Pricing agent performance by query type**: compare Hybrid queries vs pure Data queries. If Hybrid is 3× slower, it confirms the dual-tool synthesis path is the bottleneck and tells you where to optimize. |
| 5 | `fab.a2a.duration` | ms | `target_node` | A2A hop wall-clock time | `workflow.py` lines 709, 712; `a2a/clients.py` `ask_remote()` | `histogram_quantile(0.95, sum by (le, target_node)(rate(fab_a2a_duration_bucket[5m])))` | `time.perf_counter()` delta of the full A2A HTTP round-trip | **Per-agent latency breakdown**: tells you exactly which downstream agent (DataAgent, RAGAgent, ComplianceAgent) is slow. Essential for targeted performance fixes — you know which agent to scale or tune. |
| 6 | `fab.mesh.request.duration` | ms | `result`, `block_stage` | Full mesh request wall-clock time | `orchestrator.py` `handle_request()` lines 169–173 | `histogram_quantile(0.95, sum by (le)(rate(fab_mesh_request_duration_bucket[5m])))` | Orchestrator entry to final response; includes all pipeline stages | **User-facing response time**: this is what the user actually experiences. If p95 is 8 seconds, your users wait 8 seconds. The most important latency metric for SLA agreements and product acceptance. |
| 7 | `fab.output.redaction.pii_hits` | `{match}` | _(none)_ | PII tokens redacted per response | `workflow.py` `OutputRedactionExecutor.run()` line 740 | `histogram_quantile(0.95, sum by (le)(rate(fab_output_redaction_pii_hits_bucket[5m])))` | Count of `[REDACTED_*]` tokens found and replaced in output text | **Data leakage prevention signal**: if this is consistently > 0, the AI is generating responses that contain raw PII (emails, phone numbers, card numbers). A high value is a red flag — it means the model is retrieving sensitive customer data verbatim. |
| 8 | `fab.conversation.load.duration` | ms | `result` | Conversation history load wall-clock time | `orchestrator.py` lines 120, 128 | `histogram_quantile(0.95, sum by (le)(rate(fab_conversation_load_duration_bucket[5m])))` | `time.perf_counter()` delta of JSONL/Redis history fetch | **Memory backend speed**: slow history loads add latency before the LLM even starts thinking. If JSONL load is growing over time, it means conversation files are getting too large and need archiving/pruning. |
| 9 | `fab.conversation.append.duration` | ms | `result` | Conversation turn append wall-clock time | `orchestrator.py` lines 190, 194 | `histogram_quantile(0.95, sum by (le)(rate(fab_conversation_append_duration_bucket[5m])))` | `time.perf_counter()` delta of writing one turn to backend | **Write path latency**: slow appends mean users wait longer after they get their answer (the turn is being saved in the response path). Flags I/O bottlenecks in the conversation store. |
| 10 | `fab.conversation.turns_loaded` | `{turn}` | `backend` | Prior turns injected into prompt | `orchestrator.py` line 120 | `histogram_quantile(0.95, sum by (le)(rate(fab_conversation_turns_loaded_bucket[5m])))` | Integer count of turns loaded from store and prepended to LLM prompt | **Context injection volume**: tells you how much conversation history is being loaded into each prompt. If p95 is 20 turns, prompts are getting very long — directly inflates token cost and risks hitting model context limits. |
| 11 | `fab.llm.cost.usd` | `{USD}` | `agent`, `model` | Estimated USD cost per LLM call | `a2a/clients.py`; `middleware/audit_middleware.py` | `histogram_quantile(0.95, sum by (le, agent)(rate(fab_llm_cost_usd_bucket[5m])))` | `(input_tokens × price_in + output_tokens × price_out) / 1_000_000` using `Config.LLM_TOKEN_PRICING[model]` tuple | **Per-call cost accountability**: shows which agent is the most expensive per call. If `price_assist` costs $0.05 per call and you do 10,000 calls/day, that's $500/day — this metric makes that visible before the invoice arrives. |
| 12 | `fab.llm.tokens.per_call` | `{token}` | `agent`, `model`, `role` | Total tokens per LLM call (for percentile analysis) | Same as above | `histogram_quantile(0.99, sum by (le, agent)(rate(fab_llm_tokens_per_call_bucket[5m])))` | `input_tokens + output_tokens` for a single LLM call; useful for p95/p99 to catch runaway prompts | **Outlier prompt detector**: p99 tells you the worst-case token size. If p99 is 50,000 tokens but p50 is 2,000, some edge-case queries are inflating costs massively — this surfaces them so you can add prompt length guards. |

---

## In-Process Session Cost Accumulator (non-OTel)

This is **not** an OTel metric — it's an in-process dict, readable only within the `api_server` process.

| Key | Type | Description | Real-World Value |
|-----|------|-------------|-----------------|
| `_session_costs[session_id]` | dict | Per-session cost rollup: `total_input_tokens`, `total_output_tokens`, `total_tokens`, `estimated_usd`, `agents` (per-agent breakdown) | **Session cost receipt**: lets a user (or admin) see exactly what a single conversation cost — useful for showback/chargeback reporting per department or relationship manager. |

- **Populated by:** `record_llm_tokens()` in `clients.py` (api_server process only; audit_middleware writes to OTel only)
- **Read by:** `GET /api/cost/summary` endpoint in `api_server.py` via `get_cost_summary(session_id)`
- **Gated by:** `Config.ENABLE_COST_TRACKING` (in addition to `ENABLE_BUSINESS_METRICS`)

---

## Key Behavioural Notes

| Note | Detail |
|------|--------|
| **Lazy initialization** | All instruments are `None` at import time; created on first `record_*()` call after `setup_observability()` |
| **Crash-safe** | Every `record_*` function wraps OTel calls in `try/except pass` — metric failure never breaks the pipeline |
| **Prometheus name mapping** | OTel Prometheus exporter replaces `.` with `_` (e.g. `fab.a2a.calls.total` → `fab_a2a_calls_total`) |
| **`fab.a2a.calls.total` dual recording** | Intentionally recorded in both `DomainExecutor` (workflow.py) AND `ask_remote()` (clients.py); each records from its own vantage point |
| **LLM token dual recording** | `audit_middleware.py` records in remote agent node processes; `clients.py` records in api_server process AND into the `_session_costs` accumulator |
| **Conversation memory gating** | Conversation metrics only fire when `Config.ENABLE_CONVERSATION_MEMORY` causes the load/append paths to execute |
| **MCP metrics scope** | `fab.mcp.calls.total` only fires for agents in the `_MCP_AGENT_SERVICE` mapping (DataAgent → "datalayer", RAGAgent → "rag") |
| **Cost pricing lookup** | `Config.LLM_TOKEN_PRICING` is a dict keyed by model id; value is `(price_per_1M_input_tokens, price_per_1M_output_tokens)` |

---

## Proposed FAB Business Metrics (Not Yet Implemented)

These 12 new metrics would capture actual FAB banking domain outcomes — things a head of digital banking or a compliance officer would care about, not just an SRE.

**Business domains covered:** Corporate lending, revolving credit, trade finance, pricing compliance, KYC/AML, credit policy, customer 360, margin/RWA analysis.

### Proposed Counters (8)

| # | Proposed Metric Name | Attributes | Real-World Value | Where to Record | Prometheus Query |
|---|---------------------|------------|-----------------|-----------------|-----------------|
| 1 | `fab.banking.query.intent.total` | `intent` (pricing_recommendation / customer_360 / margin_analysis / rwa_impact / policy_lookup / kyc_aml / fee_schedule), `route`, `role` | **What are staff actually asking?** Know if 80% of queries are pricing checks vs policy lookups — informs training, product roadmap, and which agent to invest in. | `DomainExecutor.run()` in `workflow.py` after PriceAssistAgent returns classification | `sum by (intent)(rate(fab_banking_query_intent_total[5m]))` |
| 2 | `fab.banking.product.queries.total` | `product_type` (corporate_loan / revolving_credit / trade_finance / unknown), `role`, `route` | **Which banking products drive the most AI usage?** If trade finance queries spike after a new product launch, validates adoption. If corporate loans dominate, focus AI improvements there. | `DomainExecutor.run()` in `workflow.py` | `sum by (product_type, role)(rate(fab_banking_product_queries_total[5m]))` |
| 3 | `fab.banking.datalayer.tool.total` | `tool_name` (customer_360 / pricing_recommendation / profitability_summary / margin_analysis / rwa_impact), `result`, `role` | **Which DataLayer views are in demand?** Currently `fab.mcp.calls.total` lumps all as `"agent_invocation"`. This tells you if RMs are mostly pulling customer_360 or pricing_recommendation — drives DataLayer capacity planning. | `audit_middleware.py` — replace `"agent_invocation"` with actual tool name | `sum by (tool_name)(rate(fab_banking_datalayer_tool_total[5m]))` |
| 4 | `fab.banking.rag.category.total` | `doc_category` (pricing_policy / credit_policy / kyc_aml / product_guidelines / fee_schedule / concentration_limits / model_risk / operational_procedures), `result`, `role` | **Which policy documents are being consulted most?** If KYC/AML queries dominate, it signals regulatory pressure. If pricing_policy is queried 100×/day by credit officers, the policy document needs to be clearer or the AI answer quality needs review. | `audit_middleware.py` for RAGAgent calls | `sum by (doc_category)(rate(fab_banking_rag_category_total[5m]))` |
| 5 | `fab.banking.pricing.compliance.total` | `outcome` (compliant / non_compliant / flagged), `product_type`, `rating_tier` (BB / BBB / A / AA), `role` | **Core FAB KPI**: what % of pricing queries are compliant with policy? A rising `non_compliant` rate for BB-rated corporate loans means RMs are proposing below-floor prices — a risk management red flag. | `DomainExecutor.run()` in `workflow.py`, parsed from pricing_recommendation response | `sum by (outcome, rating_tier)(rate(fab_banking_pricing_compliance_total[5m]))` |
| 6 | `fab.banking.role.query.breakdown.total` | `role` (7 FAB roles), `intent` (data_query / policy_lookup / pricing_check / hybrid), `result` | **Adoption by role**: are credit officers actually using the pricing AI? Are branch ops officers querying policy? Identifies underused personas and helps target training/onboarding. | `orchestrator.py` `handle_request()` — role + result already available | `sum by (role, intent)(rate(fab_banking_role_query_breakdown_total[5m]))` |
| 7 | `fab.banking.compliance.bypass.total` | `role` (relationship_manager / platform_administrator / operations_manager), `bypass_reason` ("elevated_role") | **Audit trail for bypass usage**: currently buried in `fab.compliance.requests.total{result="BYPASSED"}`. A dedicated metric makes compliance gate bypass an explicit, alertable event for the audit team. | `ComplianceExecutor.run()` in `workflow.py` at the BYPASSED branch | `rate(fab_banking_compliance_bypass_total[5m])` |
| 8 | `fab.banking.guardrail.violation.total` | `violation_type` (prompt_injection / pii_email / pii_ssn / pii_credit_card / pii_phone / destructive_intent), `role`, `blocked` (true/false) | **Security breakdown by attack type AND role**: which FAB role is triggering the most PII violations? Are prompt injections coming from one specific user group? Currently `fab.guardrail.requests.total` only gives category, not role or PII sub-type. | `InputGuardrailExecutor.run()` in `workflow.py` — `screen.categories` already available; add role from state | `sum by (violation_type, role)(rate(fab_banking_guardrail_violation_total[5m]))` |

### Proposed Histograms (4)

| # | Proposed Metric Name | Unit | Attributes | Real-World Value | Where to Record | Prometheus Query (p95) |
|---|---------------------|------|------------|-----------------|-----------------|------------------------|
| 9 | `fab.banking.query.response.duration` | ms | `intent`, `route` | **Response time by query type**: does a `customer_360` hybrid query take 3× longer than a policy lookup? Lets the product team set realistic SLAs per use case (e.g. "pricing recommendations <5s, policy lookups <2s"). | `DomainExecutor.run()` in `workflow.py` alongside `record_domain_route()` | `histogram_quantile(0.95, sum by (le, intent)(rate(fab_banking_query_response_duration_bucket[5m])))` |
| 10 | `fab.banking.customer.lookup.duration` | ms | `tool_name` (customer_360 / pricing_recommendation / margin_analysis / rwa_impact / profitability_summary), `result` | **DataLayer tool SLA per operation**: is `rwa_impact` slow because it joins 5 tables? Is `customer_360` fast because it's a single view? Identifies which SQL view needs indexing. | `audit_middleware.py` — timing of DataAgent MCP tool call | `histogram_quantile(0.95, sum by (le, tool_name)(rate(fab_banking_customer_lookup_duration_bucket[5m])))` |
| 11 | `fab.banking.cost.per_role.usd` | `{USD}` | `role`, `agent` | **Cost per FAB role**: which role generates the most LLM spend? If `relationship_manager` accounts for 70% of LLM cost, the business can make an informed decision to invest in caching or prompt optimization for that persona. | `record_llm_tokens()` in `metrics.py` — add `role` attr to `_llm_cost_hist()` | `sum by (role)(rate(fab_banking_cost_per_role_usd_sum[5m]))` |
| 12 | `fab.banking.conversation.depth.total` | `{turn}` (as counter bucket) | `depth_bucket` (1_turn / 2-3_turns / 4-5_turns / 6+_turns), `role`, `route` | **Engagement depth**: are RMs having deep multi-turn pricing conversations or one-shot queries? High depth_bucket counts validate that the multi-turn memory feature is being used and delivering value. | `orchestrator.py` `handle_request()` — `turns` count already available from `record_conversation_load` | `sum by (depth_bucket, role)(rate(fab_banking_conversation_depth_total[5m]))` |

### Files to Change for Implementation

| File | Change |
|------|--------|
| `src/observability/metrics.py` | Add 8 counter + 4 histogram definitions + `record_*` wrapper functions |
| `src/mesh/workflow.py` | Wire calls in `InputGuardrailExecutor`, `ComplianceExecutor`, `DomainExecutor` |
| `src/mesh/orchestrator.py` | Wire `fab.banking.role.query.breakdown.total` + `fab.banking.conversation.depth.total` |
| `src/middleware/audit_middleware.py` | Pass real `tool_name` instead of `"agent_invocation"` for `fab.banking.datalayer.tool.total` |

### Suggested Grafana Dashboard Panels

| Panel | Metrics Used | Audience |
|-------|-------------|---------|
| **Security Posture** | `fab.banking.guardrail.violation.total` by type + role; `fab.banking.compliance.bypass.total` | CISO / Compliance Officer |
| **Pricing Intelligence** | `fab.banking.pricing.compliance.total` by outcome + rating_tier; `fab.banking.query.intent.total` | Head of Corporate Banking |
| **Platform Adoption** | `fab.banking.role.query.breakdown.total`; `fab.banking.product.queries.total`; `fab.banking.conversation.depth.total` | Digital Transformation Lead |
| **DataLayer Health** | `fab.banking.datalayer.tool.total`; `fab.banking.customer.lookup.duration` p95 by tool | Platform Engineering |
| **Cost by Role** | `fab.banking.cost.per_role.usd` sum; `fab.llm.cost.usd` p95 by agent | Head of Technology / CFO |
