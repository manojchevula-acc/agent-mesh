// TypeScript types mirroring the Python MeshResult dataclass and related
// models in src/mesh/orchestrator.py and src/auth/identity_provider.py.

export type Role = "employee" | "hr" | "leadership";
export type MeshStatus = "success" | "blocked" | "error";

export interface MeshUser {
  username: string;
  display_name: string;
  role: Role;
}

export interface MeshResult {
  answer: string;
  blocked: boolean;
  block_stage: string | null;
  trail: string[];
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
