# AgentMesh — Knowledge Base Index

Quick navigation map for the entire `agent-mesh-15062026` codebase documentation.

---

## Start Here

| File | What it answers |
|---|---|
| [project_overview.md](project_overview.md) | What is this? Entry points, config vars, tech stack |
| [request_pipeline.md](request_pipeline.md) | How does a request travel end-to-end? |

---

## Topic Areas

### Architecture
| File | What it covers |
|---|---|
| [architecture/agents.md](../architecture/agents.md) | The 4 AI agents — Compliance, Data, RAG, PriceAssist |
| [architecture/workflow_orchestration.md](../architecture/workflow_orchestration.md) | MAF WorkflowBuilder DAG, all executors, retry logic |
| [architecture/architecture.md](../architecture/architecture.md) | High-level system design |
| [architecture/architecture_implementation.md](../architecture/architecture_implementation.md) | Implementation details |
| [architecture/plan_for_handoff_orchestration_maf.md](../architecture/plan_for_handoff_orchestration_maf.md) | MAF handoff & orchestration plan |

### Features
| File | What it covers |
|---|---|
| [features/guardrails_rbac.md](../features/guardrails_rbac.md) | Deterministic filters + LLM compliance + 7-role RBAC |
| [features/hitl.md](../features/hitl.md) | Human-in-the-Loop approval flow |
| [features/mcp_integration.md](../features/mcp_integration.md) | MCP clients, DataLayer service, RAG service |
| [features/api_frontend.md](../features/api_frontend.md) | REST API routes + all React UI pages |
| [features/collaboration_tools.md](../features/collaboration_tools.md) | Cross-agent tools, depth guard, dedup cache |
| [features/reasoning_implementation.md](../features/reasoning_implementation.md) | LLM reasoning blocks implementation |
| [features/plan-response_cache.md](../features/plan-response_cache.md) | Response caching plan |
| [features/session-enforcement-plan.md](../features/session-enforcement-plan.md) | Session security plan |
| [features/plan-mcp-hub.md](../features/plan-mcp-hub.md) | MCP hub plan |

### Memory & State
| File | What it covers |
|---|---|
| [memory-and-state/memory.md](../memory-and-state/memory.md) | Conversation memory design |
| [memory-and-state/conversationstate_implementation.md](../memory-and-state/conversationstate_implementation.md) | ConversationStore implementation |
| [memory-and-state/rolling-summarization-plan.md](../memory-and-state/rolling-summarization-plan.md) | Rolling summarization design |
| [memory-and-state/plan-to-reduce-tokengrowth-conversationstate.md](../memory-and-state/plan-to-reduce-tokengrowth-conversationstate.md) | Token growth reduction strategy |

### Operations & Observability
| File | What it covers |
|---|---|
| [operations/llm_reasoning_tracing.md](../operations/llm_reasoning_tracing.md) | LLM reasoning capture + execution tracer + CLI renderer |
| [operations/observability.md](../operations/observability.md) | OTel setup, profiles, W3C baggage propagation |
| [operations/metrics_info.md](../operations/metrics_info.md) | Custom OTel metrics reference |
| [operations/trace_id_information.md](../operations/trace_id_information.md) | Trace ID and distributed tracing |
| [operations/EXECUTION.md](../operations/EXECUTION.md) | How to run the system |

### Evaluations
| File | What it covers |
|---|---|
| [evaluations/evaluation_suite.md](../evaluations/evaluation_suite.md) | All 15 evaluators, FinBen/FLARE benchmarks, red-team |
| [evaluations/workflow_evaluation_plan.md](../evaluations/workflow_evaluation_plan.md) | Evaluation planning |
| [evaluations/workflow_evaluations.md](../evaluations/workflow_evaluations.md) | Evaluation results |
| [evaluations/benchmark_analysis.md](../evaluations/benchmark_analysis.md) | Benchmark analysis |
| [evaluations/benchmark_execution_guide.md](../evaluations/benchmark_execution_guide.md) | How to run benchmarks |

### Planning
| File | What it covers |
|---|---|
| [planning/next_steps_plan.md](../planning/next_steps_plan.md) | Roadmap and prioritized next steps |
| [planning/immediate_next_steps.md](../planning/immediate_next_steps.md) | Immediate action items |

### Research
| File | What it covers |
|---|---|
| [research/FinBEN_FLARE.md](../research/FinBEN_FLARE.md) | FinBen and FLARE financial benchmark reference |
