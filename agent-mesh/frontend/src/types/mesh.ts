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

export interface FeedbackRequest {
  request_id: string;
  session_id: string;
  user: string;
  role: string;
  rating: "up" | "down";
  query: string;
  answer: string;
  route?: string;
  blocked?: boolean;
  comment?: string;
}

export interface FeedbackResponse {
  success: boolean;
  feedback_id: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: MeshResult;
  timestamp: Date;
  isLoading?: boolean;
  feedback?: { rating: "up" | "down"; comment?: string };
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

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string;
  request_id: string;
  msg: string;
  session_id?: string;
  user?: string;
  status?: string;
  agent?: string;
  node?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  tokens_estimated?: boolean;
}

export interface RequestGroup {
  request_id: string;
  trace_id: string;
  user?: string;
  session_id?: string;
  first_ts: string;
  last_ts: string;
  duration_ms: number;
  entry_count: number;
  has_error: boolean;
  has_warning: boolean;
  entries: LogEntry[];
  token_input?: number;
  token_output?: number;
  token_total?: number;
  token_estimated?: boolean;
}

export interface LogsResponse {
  groups: RequestGroup[];
  system_entries: LogEntry[];
  total_entries: number;
  unique_requests: number;
  error_count: number;
  warning_count: number;
  loggers: string[];
}

export interface AuditRecord {
  timestamp: string;
  request_id: string;
  trace_id: string;
  span_id: string;
  session_id: string;
  user: string;
  role: string;
  agent_name: string;
  input_preview: string;
  output_preview: string;
  status: string;
  latency_ms: number;
}

export interface AuditListResponse {
  records: AuditRecord[];
  total: number;
  success_count: number;
  error_count: number;
  avg_latency_ms: number;
}

export interface AuditDetailRecord extends AuditRecord {
  inputs: string[];
  output: string;
}

export interface TraceAttributes {
  target_node: string;
  prompt_length: number;
  response_length: number;
  response_preview: string;
}

export interface TraceRecord {
  event_type: string;
  name: string;
  status: string;
  timestamp: string;
  trace_id: string | null;
  span_id: string;
  parent_span_id: string | null;
  duration_ms: number;
  error: string | null;
  attributes: TraceAttributes;
}

export interface TraceListResponse {
  records: TraceRecord[];
  total: number;
  success_count: number;
  avg_duration_ms: number;
  max_duration_ms: number;
}

export interface SessionMessage {
  role: "user" | "assistant";
  content: string;
  ts?: string;
}

export interface SessionSummary {
  session_id: string;
  user: string;
  message_count: number;
  first_ts: string;
  last_ts: string;
  first_query: string;
  messages: SessionMessage[];
}

export interface ConversationsResponse {
  sessions: SessionSummary[];
  total_sessions: number;
  total_messages: number;
  unique_users: number;
}

export interface FeedbackRecord {
  feedback_id: string;
  ts: string;
  request_id: string;
  session_id: string;
  user: string;
  role: string;
  rating: "up" | "down";
  comment?: string;
  query: string;
  answer: string;
  route: string;
  blocked: boolean;
}

export interface FeedbackListResponse {
  records: FeedbackRecord[];
  total: number;
  up: number;
  down: number;
  with_comment: number;
}
