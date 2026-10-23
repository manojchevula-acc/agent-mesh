# Gernas SDK — API catalogue and Agent Mesh instrumentation map

> **Generated** by `gernas-sdk/scripts/build_architecture_doc.py` from the source and `checklist.md` (re-verification 2026-10-18). Do not edit by hand; re-run the script after code changes. Line numbers are exact for the committed source.

## 1. Public APIs

| API | Group | Defined at | Span (registry) | Agent Mesh / service call sites | Tests |
|---|---|---|---|---|---|
| `init_gernas` | Setup | `gernas-sdk/src/gernas/tracer.py:254` | — | 8 | 1 |
| `GernasConfig` | Setup | `gernas-sdk/src/gernas/config.py:50` | — | 0 | 11 |
| `GernasHandle` | Setup | `gernas-sdk/src/gernas/tracer.py:202` | — | 0 | 0 |
| `trace_use_case` | Decorator | `gernas-sdk/src/gernas/decorators/use_case.py:73` | `use_case.request` (SERVER) | 1 | 3 |
| `trace_agent` | Decorator | `gernas-sdk/src/gernas/decorators/agent.py:181` | `agent.run` (INTERNAL) | 1 | 38 |
| `trace_step` | Decorator | `gernas-sdk/src/gernas/decorators/planning.py:64` | `agent.step` (INTERNAL) | 9 | 4 |
| `trace_planning` | Decorator | `gernas-sdk/src/gernas/decorators/planning.py:146` | `agent.plan` (INTERNAL) | 0 | 2 |
| `trace_delegation` | Decorator | `gernas-sdk/src/gernas/decorators/delegation.py:64` | `agent.delegate` (CLIENT) | 1 | 3 |
| `trace_llm` | Decorator | `gernas-sdk/src/gernas/decorators/llm.py:235` | `llm.call` (CLIENT) | 4 | 15 |
| `trace_embedding` | Decorator | `gernas-sdk/src/gernas/decorators/llm.py:319` | `embedding.create` (CLIENT) | 0 | 1 |
| `trace_tool` | Decorator | `gernas-sdk/src/gernas/decorators/tool.py:103` | `tool.call` (CLIENT) | 4 | 32 |
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
| `mark_fallback` | Run helper | `gernas-sdk/src/gernas/decorators/llm.py:118` | — | 0 | 2 |
| `record_evaluation` | Run helper | `gernas-sdk/src/gernas/evaluation.py:30` | `evaluation.score` (INTERNAL) | 0 | 3 |
| `annotate` | Run helper | `gernas-sdk/src/gernas/__init__.py:58` | — | 0 | 4 |
| `request_context` | Context | `gernas-sdk/src/gernas/context.py:76` | — | 1 | 11 |
| `correlation` | Context | `gernas-sdk/src/gernas/context.py:218` | — | 1 | 2 |
| `inject_context` | Context | `gernas-sdk/src/gernas/context.py:151` | — | 0 | 6 |
| `extract_context` | Context | `gernas-sdk/src/gernas/context.py:163` | — | 0 | 1 |
| `attach_headers` | Context | `gernas-sdk/src/gernas/context.py:172` | — | 0 | 3 |
| `gernas_task` | Context | `gernas-sdk/src/gernas/context.py:245` | — | 1 | 1 |
| `wrap_in_context` | Context | `gernas-sdk/src/gernas/context.py:250` | — | 0 | 1 |
| `app_context` | Context | `gernas-sdk/src/gernas/context.py:194` | — | 1 | 3 |
| `app_value` | Context | `gernas-sdk/src/gernas/context.py:213` | — | 13 | 1 |
| `CorrelationLogFilter` | Logging | `gernas-sdk/src/gernas/logs.py:41` | — | 1 | 3 |
| `set_redaction_hook` | Privacy | `gernas-sdk/src/gernas/redaction.py:35` | — | 0 | 1 |
| `gernas_middleware` | Adapter (MAF) | `gernas-sdk/src/gernas/adapters/agent_framework.py:140` | — | 1 | 3 |
| `disable_native_telemetry` | Adapter (MAF) | `gernas-sdk/src/gernas/adapters/agent_framework.py:126` | — | 4 | 1 |
| `GernasA2AInterceptor` | Adapter (A2A) | `gernas-sdk/src/gernas/adapters/a2a.py:19` | — | 1 | 1 |
| `GernasASGIMiddleware` | Adapter (ASGI) | `gernas-sdk/src/gernas/adapters/asgi.py:30` | `{method} {route}` (SERVER) | 3 | 4 |
| `GernasFastMCPMiddleware` | Adapter (MCP server) | `gernas-sdk/src/gernas/adapters/mcp.py:60` | — | 1 | 2 |
| `mcp_meta_context` | Adapter (MCP server) | `gernas-sdk/src/gernas/adapters/mcp.py:41` | — | 1 | 1 |
| `GernasCallbackHandler` | Adapter (LangChain) | `gernas-sdk/src/gernas/adapters/langchain.py:66` | — | 0 | 1 |

## 2. Call sites (Agent Mesh, DataLayer MCP, RAG service)

Every service imports `gernas` directly (no integration module in between); the Helper column is the SDK symbol as written at the call site.

| Service | Location | Where | How | Helper | SDK API |
|---|---|---|---|---|---|
| Agent Mesh | `agent-mesh/a2a_server.py:120` | `main` | call | `gernas.init_gernas` | `init_gernas` |
| Agent Mesh | `agent-mesh/a2a_server.py:125` | `main` | call | `gernas.disable_native_telemetry` | `disable_native_telemetry` |
| Agent Mesh | `agent-mesh/api_server.py:55` | `<module> → _telemetry` | call | `gernas.init_gernas` | `init_gernas` |
| Agent Mesh | `agent-mesh/api_server.py:61` | `<module>` | call | `gernas.disable_native_telemetry` | `disable_native_telemetry` |
| Agent Mesh | `agent-mesh/api_server.py:1160` | `<module>` | argument | `gernas.GernasASGIMiddleware` | `GernasASGIMiddleware` |
| Agent Mesh | `agent-mesh/demos/gernas_e2e_queries.py:38` | `<module> → telemetry` | call | `gernas.init_gernas` | `init_gernas` |
| Agent Mesh | `agent-mesh/demos/gernas_e2e_queries.py:39` | `<module>` | call | `gernas.disable_native_telemetry` | `disable_native_telemetry` |
| Agent Mesh | `agent-mesh/devui_app.py:53` | `<module>` | call | `gernas.init_gernas` | `init_gernas` |
| Agent Mesh | `agent-mesh/run.py:31` | `<module> → _telemetry` | call | `gernas.init_gernas` | `init_gernas` |
| Agent Mesh | `agent-mesh/run.py:37` | `<module>` | call | `gernas.disable_native_telemetry` | `disable_native_telemetry` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:50` | `get_remote_agent` | call | `gernas.GernasA2AInterceptor` | `GernasA2AInterceptor` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:54` | `ask_remote` | decorator | `gernas.trace_delegation` | `trace_delegation` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:78` | `ask_remote` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/a2a/clients.py:88` | `ask_remote` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/a2a/hosting.py:85` | `build_starlette_app` | argument | `gernas.GernasASGIMiddleware` | `GernasASGIMiddleware` |
| Agent Mesh | `agent-mesh/src/agents/agent_factory.py:69` | `create_demo_agent` | call | `gernas.gernas_middleware` | `gernas_middleware` |
| Agent Mesh | `agent-mesh/src/cache/cache_judge.py:28` | `_post_chat` | decorator | `gernas.trace_llm` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:44` | `_post_chat` | decorator | `gernas.trace_llm` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:50` | `_post_chat_sync` | decorator | `gernas.trace_llm` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:325` | `_chat_completion_sync` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/cache/entity_extractor.py:334` | `_chat_completion_sync` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/cache/semantic_cache.py:143` | `SemanticCacheStore.lookup_top_n` | decorator | `gernas.trace_retrieval` | `trace_retrieval` |
| Agent Mesh | `agent-mesh/src/guardrails/deterministic_filters.py:126` | `<module> → screen_input` | call | `gernas.trace_guardrail` | `trace_guardrail` |
| Agent Mesh | `agent-mesh/src/guardrails/deterministic_filters.py:140` | `redact_output` | decorator | `gernas.trace_content_safety` | `trace_content_safety` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:72` | `ApprovalStore.create` | call | `gernas.start_hitl` | `start_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:104` | `ApprovalStore.backfill` | call | `gernas.start_hitl` | `start_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:125` | `ApprovalStore.wait_for_approval` | call | `gernas.complete_hitl` | `complete_hitl` |
| Agent Mesh | `agent-mesh/src/hitl/approval_store.py:143` | `ApprovalStore._signal` | call | `gernas.complete_hitl` | `complete_hitl` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:45` | `ConversationStore.load_with_summary` | decorator | `gernas.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:56` | `ConversationStore.save_summary` | decorator | `gernas.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/conversation_store.py:81` | `ConversationStore.append_turn_rich` | decorator | `gernas.trace_memory` | `trace_memory` |
| Agent Mesh | `agent-mesh/src/memory/summarizer.py:31` | `_post_chat` | decorator | `gernas.trace_llm` | `trace_llm` |
| Agent Mesh | `agent-mesh/src/memory/summarizer.py:61` | `summarize_and_persist` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:107` | `_run_workflow` | decorator | `gernas.trace_agent` | `trace_agent` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:123` | `handle_request` | decorator | `gernas.trace_use_case` | `trace_use_case` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:145` | `handle_request → _app_ctx` | call | `gernas.app_context` | `app_context` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:150` | `handle_request → _gernas_ctx` | call | `gernas.request_context` | `request_context` |
| Agent Mesh | `agent-mesh/src/mesh/orchestrator.py:460` | `handle_request` | call | `gernas.gernas_task` | `gernas_task` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:104` | `_role_recognised` | decorator | `gernas.trace_guardrail` | `trace_guardrail` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:226` | `InputGuardrailExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:240` | `InputGuardrailExecutor.run` | call | `gernas.set_termination_reason` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:288` | `RBACValidationExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:300` | `RBACValidationExecutor.run` | call | `gernas.set_termination_reason` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:358` | `CacheCheckExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:775` | `ComplianceExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:837` | `ComplianceExecutor.run` | call | `gernas.set_termination_reason` | `set_termination_reason` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:841` | `ComplianceExecutor.run` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:843` | `ComplianceExecutor.run` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:863` | `ComplianceExecutor.run` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:865` | `ComplianceExecutor.run` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:959` | `DomainExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1084` | `DomainExecutor.run` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1093` | `DomainExecutor.run` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1109` | `DomainExecutor.run` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1178` | `DomainExecutor.run` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/mesh/workflow.py:1365` | `OutputRedactionExecutor.run` | decorator | `gernas.trace_step` | `trace_step` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:29` | `_trace_ids → corr` | call | `gernas.correlation` | `correlation` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:148` | `AuditMiddleware.process` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:149` | `AuditMiddleware.process` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:150` | `AuditMiddleware.process` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/middleware/audit_middleware.py:152` | `AuditMiddleware.process` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/middleware/tool_call_logger.py:33` | `_request_id` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:78` | `_consult_peer` | call | `gernas.record_retry` | `record_retry` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:102` | `_consult_peer` | call | `gernas.app_value` | `app_value` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:136` | `query_structured_data` | decorator | `gernas.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:149` | `query_knowledge_base` | decorator | `gernas.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:165` | `submit_pricing_change` | decorator | `gernas.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/tools/collaboration_tools.py:184` | `adjust_credit_limit` | decorator | `gernas.trace_tool` | `trace_tool` |
| Agent Mesh | `agent-mesh/src/utils/logging_config.py:153` | `configure_logging → trace_filter` | call | `gernas.CorrelationLogFilter` | `CorrelationLogFilter` |
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
| RAG service | `rag-as-a-service/src/gernas_rag/main.py:91` | `create_app` | argument | `gernas.GernasASGIMiddleware` | `GernasASGIMiddleware` |

## 3. The five SOW aspects (derived from `checklist.md` §3 / §3A)

| Aspect | Principle | Rows | Verified | Partial | Not started | Failed | Blocked | N/A | Status |
|---|---|---|---|---|---|---|---|---|---|
| A1 | OpenTelemetry | 17 | 11 | 6 | 0 | 0 | 0 | 0 | Partial |
| A2 | Standardized GERNAS semantic conventions | 9 | 7 | 2 | 0 | 0 | 0 | 0 | Partial |
| A3 | Standard trace/span context | 6 | 3 | 3 | 0 | 0 | 0 | 0 | Partial |
| A4 | Exporter abstraction | 4 | 2 | 1 | 0 | 0 | 1 | 0 | Partial |
| A5 | Centralized telemetry gateway/collectors | 6 | 2 | 1 | 0 | 0 | 3 | 0 | Partial |
| X | Outside the five aspects | 23 | 0 | 2 | 18 | 0 | 1 | 2 | Not delivered |
