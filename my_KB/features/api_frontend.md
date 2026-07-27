# REST API & React Frontend

---

## API Server

**File:** `api_server.py`  
**Framework:** Starlette + uvicorn  
**Default port:** 8000

---

## REST API Routes

### Auth & Users
| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/api/users` | List all demo users with their roles |
| `POST` | `/api/login` | Resolve `{username}` → `User` object with role |

### Query
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/query` | Submit query → full `MeshResult` (blocking) |
| `POST` | `/api/query/stream` | Submit query → SSE stream of pipeline stage events |

**Request body (both):**
```json
{
  "query": "What is the pricing for...",
  "user": "alice",
  "session_id": "abc-123"
}
```

**`MeshResult` response fields:**
- `answer` — final redacted answer
- `route` — `"Data Layer Service"` | `"RAG Service"` | `"Hybrid"`
- `request_id`, `trace_id`, `session_id`
- `events` — array of `ExecutionEvent` objects (pipeline trace)
- `llm_reasoning` — array of `ReasoningEntry` objects (AI reasoning from all agents)

### SSE Stream Event Types (`/api/query/stream`)
| Event type | When |
|---|---|
| `stage` | Each executor starts/completes |
| `reasoning` | LLM reasoning block extracted |
| `hitl` | HITL approval required (credit officer) |
| `result` | Final answer ready |
| `done` | Stream closed |
| `error` | Unhandled error |

### Mesh Status
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/mesh/status` | Fan-out health check to all 4 A2A nodes |

### Conversations
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conversations/{session_id}` | Load conversation history (ownership-enforced) |
| `GET` | `/api/conversations/list` | All sessions with full message history |

### Logs & Audit
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/logs` | Structured log data grouped by `request_id` (token data injected from audit) |
| `GET` | `/api/audit` | Audit trail records — slim, newest-first |
| `GET` | `/api/audit/{request_id}` | Full audit record by request ID |
| `GET` | `/api/traces` | OTel trace span records |

### Feedback
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/feedback` | Submit thumbs up/down with Q&A pair |
| `GET` | `/api/feedback/list` | All feedback records |
| `GET` | `/api/feedback/stats` | Aggregate counts (total, positive, negative) |

### HITL Approvals
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/approvals/{id}` | Get pending approval details |
| `POST` | `/api/approvals/{id}/approve` | Approve → resumes domain execution |
| `POST` | `/api/approvals/{id}/reject` | Reject → returns declined message |

---

## React Frontend

**Directory:** `frontend/`  
**Stack:** React 18 + TypeScript + Vite + Tailwind CSS + TanStack Query

---

## Pages

| Page | File | Description |
|---|---|---|
| Home | `pages/HomePage.tsx` | Landing / intro page |
| Login | `pages/LoginPage.tsx` | Username-based login (selects demo user) |
| Signup | `pages/SignupPage.tsx` | Signup flow |
| **Chat** | `pages/ChatPage.tsx` | Main chat interface — SSE consumption, execution panel, reasoning panel |
| Audit Dashboard | `pages/AuditDashboardPage.tsx` | Audit trail viewer — all requests with PII-scrubbed details |
| Trace Dashboard | `pages/TraceDashboardPage.tsx` | OTel distributed trace viewer |
| Logs Dashboard | `pages/LogsDashboardPage.tsx` | Structured log browser grouped by request |
| Conversations | `pages/ConversationsDashboardPage.tsx` | Session browser with message replay |
| Feedback | `pages/FeedbackDashboardPage.tsx` | Feedback analytics — thumbs up/down stats |
| Mesh Status | `pages/MeshStatusPage.tsx` | Live A2A node health (fan-out to all 4 agents) |
| Request Activity | `pages/RequestActivityPage.tsx` | Request activity log |
| HITL Approval | `pages/ApprovalPage.tsx` | Standalone approval page (shareable link for reviewers) |

---

## Key Chat Components

| Component | File | What it does |
|---|---|---|
| `ExecutionPanel` | `components/ExecutionPanel.tsx` | Real-time pipeline stage visualizer consuming SSE events |
| `LLMReasoningPanel` | `components/LLMReasoningPanel.tsx` | AI Reasoning transparency panel — renders all `llm_reasoning` entries by agent |
| `PipelineTrail` | `components/PipelineTrail.tsx` | Visual breadcrumb showing completed pipeline stages |
| `ApprovalModal` | `components/ApprovalModal.tsx` | In-chat HITL approval UI (appears on `hitl` SSE event) |
| `SecurityBadge` | `components/SecurityBadge.tsx` | Compliance/guardrail status badge per response |

---

## API Client

**File:** `frontend/src/api/mesh.ts`

Typed API client for all routes. Uses TanStack Query for caching and refetching on the dashboard pages. Handles SSE streaming via `EventSource` in `ChatPage`.

---

## Auth Context

**File:** `frontend/src/contexts/AuthContext.tsx`

Stores the logged-in user + role. Role is used throughout the UI to show/hide role-specific features (e.g. HITL approval panel only visible to approvers).
