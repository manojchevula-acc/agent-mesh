# Gernas SDK — API catalogue and Agent Mesh instrumentation map

> **Generated** by `gernas-sdk/scripts/build_architecture_doc.py` from the source and `checklist.md` (re-verification 2026-10-18). Do not edit by hand; re-run the script after code changes. Line numbers are exact for the committed source.

## 1. Public APIs

| API | Group | Defined at | Span (registry) | Agent Mesh / service call sites | Tests |
|---|---|---|---|---|---|
| `init_gernas` | Setup | `gernas-sdk/src/gernas/tracer.py:254` | — | 8 | 1 |
| `GernasConfig` | Setup | `gernas-sdk/src/gernas/config.py:50` | — | 5 | 11 |
| `GernasHandle` | Setup | `gernas-sdk/src/gernas/tracer.py:202` | — | 3 | 0 |
| `trace_use_case` | Decorator | `gernas-sdk/src/gernas/decorators/use_case.py:73` | `use_case.request` (SERVER) | 1 | 3 |
| `trace_agent` | Decorator | `gernas-sdk/src/gernas/decorators/agent.py:181` | `agent.run` (INTERNAL) | 1 | 37 |
| `trace_step` | Decorator | `gernas-sdk/src/gernas/decorators/planning.py:64` | `agent.step` (INTERNAL) | 9 | 4 |
| `trace_planning` | Decorator | `gernas-sdk/src/gernas/decorators/planning.py:146` | `agent.plan` (INTERNAL) | 0 | 2 |
| `trace_delegation` | Decorator | `gernas-sdk/src/gernas/decorators/delegation.py:64` | `agent.delegate` (CLIENT) | 1 | 3 |
| `trace_llm` | Decorator | `gernas-sdk/src/gernas/decorators/llm.py:217` | `llm.call` (CLIENT) | 4 | 14 |
| `trace_embedding` | Decorator | `gernas-sdk/src/gernas/decorators/llm.py:301` | `embedding.create` (CLIENT) | 0 | 1 |
| `trace_tool` | Decorator | `gernas-sdk/src/gernas/decorators/tool.py:103` | `tool.call` (CLIENT) | 4 | 31 |
| `trace_retrieval` | Decorator | `gernas-sdk/src/gernas/decorators/retrieval.py:107` | `retrieval.search` (CLIENT) | 3 | 3 |
| `trace_document` | Decorator | `gernas-sdk/src/gernas/decorators/document.py:75` | `document.process` (INTERNAL) | 2 | 4 |
| `trace_memory` | Decorator | `gernas-sdk/src/gernas/decorators/memory.py:49` | `memory.access` (INTERNAL) | 3 | 3 |
| `trace_guardrail` | Decorator | `gernas-sdk/src/gernas/decorators/safety.py:111` | `guardrail.check` (INTERNAL) | 2 | 5 |
| `trace_content_safety` | Decorator | `gernas-sdk/src/gernas/decorators/safety.py:186` | `content_safety.check` (INTERNAL) | 1 | 3 |
| `trace_hitl` | Decorator | `gernas-sdk/src/gernas/hitl.py:144` | `hitl.approval` (INTERNAL) | 0 | 3 |
| `start_hitl` | Run helper | `gernas-sdk/src/gernas/hitl.py:52` | `hitl.approval` (INTERNAL) | 2 | 3 |
| `complete_hitl` | Run helper | `gernas-sdk/src/gernas/hitl.py:79` | — | 2 | 3 |
| `record_retry` | Run helper | `gernas-sdk/src/gernas/run_state.py:150` | — | 6 | 3 |
| `set_termination_reason` | Run helper | `gernas-sdk/src/gernas/run_state.py:157` | — | 3 | 2 |
| `mark_fallback` | Run helper | `gernas-sdk/src/gernas/decorators/llm.py:102` | — | 0 | 2 |
| `record_evaluation` | Run helper | `gernas-sdk/src/gernas/evaluation.py:30` | `evaluation.score` (INTERNAL) | 0 | 3 |
| `annotate` | Run helper | `gernas-sdk/src/gernas/__init__.py:55` | — | 0 | 4 |
| `request_context` | Context | `gernas-sdk/src/gernas/context.py:74` | — | 1 | 9 |
| `correlation` | Context | `gernas-sdk/src/gernas/context.py:188` | — | 2 | 2 |
| `inject_context` | Context | `gernas-sdk/src/gernas/context.py:149` | — | 0 | 5 |
| `extract_context` | Context | `gernas-sdk/src/gernas/context.py:161` | — | 0 | 1 |
| `attach_headers` | Context | `gernas-sdk/src/gernas/context.py:170` | — | 0 | 2 |
| `gernas_task` | Context | `gernas-sdk/src/gernas/context.py:215` | — | 1 | 1 |
| `wrap_in_context` | Context | `gernas-sdk/src/gernas/context.py:220` | — | 0 | 1 |
| `set_redaction_hook` | Privacy | `gernas-sdk/src/gernas/redaction.py:35` | — | 0 | 1 |
| `gernas_middleware` | Adapter (MAF) | `gernas-sdk/src/gernas/adapters/agent_framework.py:140` | — | 1 | 3 |
| `disable_native_telemetry` | Adapter (MAF) | `gernas-sdk/src/gernas/adapters/agent_framework.py:126` | — | 5 | 1 |
| `GernasA2AInterceptor` | Adapter (A2A) | `gernas-sdk/src/gernas/adapters/a2a.py:19` | — | 1 | 1 |
| `GernasASGIMiddleware` | Adapter (ASGI) | `gernas-sdk/src/gernas/adapters/asgi.py:30` | `{method} {route}` (SERVER) | 3 | 4 |
| `GernasFastMCPMiddleware` | Adapter (MCP server) | `gernas-sdk/src/gernas/adapters/mcp.py:60` | — | 1 | 2 |
| `mcp_meta_context` | Adapter (MCP server) | `gernas-sdk/src/gernas/adapters/mcp.py:41` | — | 1 | 1 |
| `GernasCallbackHandler` | Adapter (LangChain) | `gernas-sdk/src/gernas/adapters/langchain.py:66` | — | 0 | 1 |

## 2. Call sites (Agent Mesh, DataLayer MCP, RAG service)

`_gt` is the mesh's single integration module `agent-mesh/src/observability/gernas_telemetry.py`; the SDK API column is what that helper delegates to.

| Service | Location | Where | How | Helper | SDK API |
|---|---|---|---|---|---|
| Agent Mesh | `agent-mesh/a2a_server.py:113` | `main` | call | `_gt.init_telemetry` | `init_gernas`, `disable_native_telemetry`, `GernasConfig` |
| Agent Mesh | `agent-mesh/api_server.py:50` | `<module>` | call | `_gt.init_telemetry` | `init_gernas`, `disable_native_telemetry`, `GernasConfig` |
| Agent Mesh | `agent-mesh/api_server.py:836` | `_lifespan` | call | `_gt.shutdown` | `GernasHandle` |
| Agent Mesh | `agent-mesh/api_server.py:1147` | `<module>` | call | `_gt.asgi_middleware` | `GernasASGIMiddleware` |
| Agent Mesh | `agent-mesh/api_server.py:1148` | `<module>` | call | `_gt.asgi_middleware` | `GernasASGIMiddleware` |
| Agent Mesh | `agent-mesh/api_server.py:1192` | `main → obs_dest` | call | `_gt.describe` | — |
| Agent Mesh | `agent-mesh/demos/gernas_e2e_queries.py:32` | `<module>` | call | `_gt.init_telemetry` | `init_gernas`, `disable_native_telemetry`, `GernasConfig` |
| Agent Mesh | `agent-mesh/demos/gernas_e2e_queries.py:60` | `main` | call | `_gt.shutdown` | `GernasHandle` |
| Agent Mesh | `agent-mesh/devui_app.py:49` | `<module>` | call | `_gt.init_telemetry` | `init_gernas`, `disable_native_telemetry`, `GernasConfig` |
| Agent Mesh | `agent-mesh/run.py:26` | `<module>` | call | `_gt.init_telemetry` | `init_gernas`, `disable_native_telemetry`, `GernasConfig` |
| Agent Mesh | `agent-mesh/run.py:178` | `main` | call | `_gt.shutdown` | `GernasHandle` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:47` | `get_remote_agent → interceptor` | call | `_gt.a2a_interceptor` | `GernasA2AInterceptor` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:59` | `ask_remote` | decorator | `_gt.trace_delegation` | `trace_delegation` |
| Agent Mesh | `agent-mesh/src/a2a/hosting.py:83` | `build_starlette_app → gernas_mw` | call | `_gt.asgi_middleware` | `GernasASGIMiddleware` |
| Agent Mesh | `agent-mesh/src/agents/agent_factory.py:65` | `create_demo_agent` | call | `_gt.agent_middleware` | `gernas_middleware` |
| Agent Mesh | `agent-mesh/src/cache/cache_judge.py:87` | `llm_cache_judge` | call | `_gt.chat_post` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:266` | `extract_entities` | call | `_gt.chat_post` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:309` | `_chat_completion_sync → resp` | call | `_gt.chat_post_sync` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:312` | `_chat_completion_sync` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:321` | `_chat_completion_sync` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/cache/semantic_cache.py:143` | `SemanticCacheStore.lookup_top_n` | decorator | `_gt.trace_retrieval` | `trace_retrieval` |
| Agent Mesh | `agent-mesh/src/guardrails/deterministic_filters.py:126` | `<module> → screen_input` | call | `_gt.trace_guardrail` | `trace_guardrail` |
| Agent Mesh | `agent-mesh/src/guardrails/deterministic_filters.py:140` | `redact_output` | decorator | `_gt.trace_content_safety` | `trace_content_safety` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:72` | `ApprovalStore.create` | call | `_gt.start_hitl` | `start_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:104` | `ApprovalStore.backfill` | call | `_gt.start_hitl` | `start_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:125` | `ApprovalStore.wait_for_approval` | call | `_gt.complete_hitl` | `complete_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:143` | `ApprovalStore._signal` | call | `_gt.complete_hitl` | `complete_hitl` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:45` | `ConversationStore.load_with_summary` | decorator | `_gt.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:56` | `ConversationStore.save_summary` | decorator | `_gt.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:81` | `ConversationStore.append_turn_rich` | decorator | `_gt.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/summarizer.py:49` | `_call_groq` | call | `_gt.chat_post` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/memory/summarizer.py:56` | `summarize_and_persist` | decorator | `_gt.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:108` | `_run_workflow` | decorator | `_gt.trace_mesh_run` | `trace_agent` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:114` | `handle_request` | decorator | `_gt.trace_use_case` | `trace_use_case` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:140` | `handle_request → _gernas_ctx` | call | `_gt.request_context` | `request_context` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:449` | `handle_request` | call | `_gt.spawn` | `gernas_task` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:105` | `_role_recognised` | decorator | `_gt.trace_guardrail` | `trace_guardrail` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:227` | `InputGuardrailExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:241` | `InputGuardrailExecutor.run` | call | `_gt.mark_policy_block` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:289` | `RBACValidationExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:301` | `RBACValidationExecutor.run` | call | `_gt.mark_policy_block` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:359` | `CacheCheckExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:776` | `ComplianceExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:838` | `ComplianceExecutor.run` | call | `_gt.mark_policy_block` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:962` | `DomainExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1087` | `DomainExecutor.run` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1096` | `DomainExecutor.run` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1112` | `DomainExecutor.run` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1369` | `OutputRedactionExecutor.run` | decorator | `_gt.trace_workflow_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:30` | `_trace_ids → corr` | call | `_gt.correlation` | `correlation` |
| Agent Mesh | `agent-mesh/src/observability/logging_config.py:120` | `TraceContextFilter.filter → corr` | call | `_gt.correlation` | `correlation` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:78` | `_consult_peer` | call | `_gt.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:137` | `query_structured_data` | decorator | `_gt.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:150` | `query_knowledge_base` | decorator | `_gt.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:166` | `submit_pricing_change` | decorator | `_gt.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:185` | `adjust_credit_limit` | decorator | `_gt.trace_tool` | `trace_tool` |
| DataLayer MCP | `datalayer-as-service/mcp_server/server.py:76` | `<module>` | call | `gernas.init_gernas` | `init_gernas` |
| DataLayer MCP | `datalayer-as-service/mcp_server/server.py:78` | `<module> → _gernas_mw` | call | `gernas.GernasFastMCPMiddleware` | `GernasFastMCPMiddleware` |
| RAG service | `rag-as-a-service/mcp_integration/server.py:53` | `<module>` | call | `gernas.init_gernas` | `init_gernas` |
| RAG service | `rag-as-a-service/mcp_integration/server.py:54` | `<module> → _trace_retrieval` | call | `gernas.trace_retrieval` | `trace_retrieval` |
| RAG service | `rag-as-a-service/mcp_integration/server.py:173` | `search_documents` | call | `gernas.mcp_meta_context` | `mcp_meta_context` |
| RAG service | `rag-as-a-service/mcp_integration/server.py:177` | `_search_documents` | decorator | `gernas.trace_retrieval` | `trace_retrieval` |
| RAG service | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py:21` | `<module> → _trace_document` | call | `gernas.trace_document` | `trace_document` |
| RAG service | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py:25` | `<module> → _trace_extraction` | call | `gernas.trace_step` | `trace_step` |
| RAG service | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py:53` | `IngestionPipeline.ingest_file` | decorator | `gernas.trace_document` | `trace_document` |
| RAG service | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py:103` | `IngestionPipeline._extract` | decorator | `gernas.trace_step` | `trace_step` |
| RAG service | `rag-as-a-service/src/gernas_rag/main.py:31` | `lifespan` | call | `gernas.init_gernas` | `init_gernas` |

## 3. The five SOW aspects (derived from `checklist.md` §3 / §3A)

| Aspect | Principle | Rows | Verified | Partial | Not started | Failed | Blocked | N/A | Status |
|---|---|---|---|---|---|---|---|---|---|
| A1 | OpenTelemetry | 17 | 11 | 6 | 0 | 0 | 0 | 0 | Partial |
| A2 | Standardized GERNAS semantic conventions | 9 | 7 | 2 | 0 | 0 | 0 | 0 | Partial |
| A3 | Standard trace/span context | 6 | 3 | 3 | 0 | 0 | 0 | 0 | Partial |
| A4 | Exporter abstraction | 4 | 2 | 1 | 0 | 0 | 1 | 0 | Partial |
| A5 | Centralized telemetry gateway/collectors | 6 | 2 | 1 | 0 | 0 | 3 | 0 | Partial |
| X | Outside the five aspects | 23 | 0 | 2 | 18 | 0 | 1 | 2 | Not delivered |
