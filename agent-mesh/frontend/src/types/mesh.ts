// TypeScript types mirroring the Python MeshResult dataclass and related
// models in src/mesh/orchestrator.py and src/auth/identity_provider.py.

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
  confidence?: number | null;
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
  // Execution summary from the tracer (api_server.py wires this)
  request_id?: string;
  domain?: string | null;
  route?: string | null;
  execution_path?: string[];
  agents_invoked?: number;
  tools_used?: number;
  total_duration_ms?: number;
  confidence?: number | null;
  // Full step-by-step event stream for the execution transparency panel
  events?: ExecutionEvent[];
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
}

export interface LoginRequest {
  username: string;
}
