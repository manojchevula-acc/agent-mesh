// TypeScript types mirroring the Python MeshResult dataclass and related
// models in src/mesh/orchestrator.py and src/auth/identity_provider.py.

// Mirrors LLMReasoningEntry dataclass in src/tracing/llm_reasoning.py
export interface LLMReasoningData {
  // intent_routing phase
  intent?: "data" | "knowledge" | "hybrid";
  data_signals?: string[];
  rag_signals?: string[];
  rationale?: string;
  confidence?: number;
  // synthesis phase
  sources_used?: string[];
  key_findings?: string[];
  answer_rationale?: string;
  // safety_review phase
  checks?: string[];
  risk_signals?: string[];
  decision?: string;
  // tool_selection phase (data / rag agents)
  tool_selected?: string;
  customer_id?: string;
  query_intent?: string;
  search_query?: string;
  knowledge_domain?: string;
  // fallback for unstructured blocks
  raw?: string;
  [key: string]: unknown;
}

export interface LLMReasoningEntry {
  agent: string;   // "compliance" | "price_assist" | "data" | "rag"
  phase: string;   // "safety_review" | "intent_routing" | "synthesis"
  data: LLMReasoningData;
  timestamp?: string;
}

export type Role =
  | "customer"
  | "relationship_manager"
  | "branch_operations_officer"
  | "credit_officer"
  | "compliance_officer"
  | "operations_manager"
  | "platform_administrator";

export type MeshStatus = "success" | "blocked" | "error";

export interface MeshUser {
  username: string;
  display_name: string;
  role: Role;
}

// Mirrors ExecutionEvent dataclass in src/tracing/execution_trace.py
export interface ExecutionEvent {
  stage: string;
  status: string;           // "started" | "completed" | "blocked" | "failed"
  message?: string;
  result?: string | null;
  rationale?: string[];
  checks?: string[];
  metadata?: Record<string, unknown>;
  timestamp?: string;
  duration_ms?: number | null;
}

export interface MeshResult {
  answer: string;
  blocked: boolean;
  block_stage: string | null;
  trail: string[];
  // Conversation this turn belongs to. The first response pins it; echo it back
  // on subsequent queries to continue the thread.
  session_id?: string;
  // Execution summary from the tracer (api_server.py wires this)
  request_id?: string;
  domain?: string | null;
  route?: string | null;
  execution_path?: string[];
  agents_invoked?: number;
  tools_used?: number;
  total_duration_ms?: number;
  // Full step-by-step event stream for the execution transparency panel
  events?: ExecutionEvent[];
  // Captured LLM reasoning entries for the AI Reasoning explainability panel
  llm_reasoning?: LLMReasoningEntry[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: MeshResult;
  timestamp: Date;
  isLoading?: boolean;
}

export interface NodeHealth {
  name: string;
  port: number;
  status: "ok" | "error" | "unknown";
  uptime_seconds: number | null;
  model: string | null;
  url: string;
  error?: string;
}

export interface QueryRequest {
  username: string;
  query: string;
  session_id?: string;
}

// One stored conversation message (mirrors the JSONL records persisted server-side).
export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  ts?: string;
}

export interface ConversationHistory {
  session_id: string;
  messages: ConversationMessage[];
}

export interface LoginRequest {
  username: string;
}
