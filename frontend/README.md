# GERNAS RAG — React Frontend

Production-grade React UI for the GERNAS RAG service (FAB Policy & Regulatory
Assistant). This is the **only** frontend — the previous Streamlit prototype has
been removed. It's an interactive SPA, with light/dark theming and an
animated marketing landing page, that talks to the same FastAPI backend.

## Stack

| Concern        | Choice                                   |
| -------------- | ---------------------------------------- |
| Build tool     | Vite 5                                   |
| Language       | TypeScript (strict)                      |
| UI             | React 18 + Tailwind CSS 3                |
| Server state   | TanStack Query 5                         |
| Routing        | React Router 6                           |
| HTTP           | Axios (shared client + error normaliser) |
| Icons          | lucide-react                             |
| Markdown       | react-markdown + remark-gfm              |

## Features

- **Landing page** (`/`) — animated marketing home with hero, feature grid,
  "how it works" and CTA sections.
- **Auth** (`/login`, `/signup`) — client-side mock auth (localStorage) gating
  the app routes under `/app/*`. Swap `AuthContext` for real API calls when an
  auth backend exists.
- **Light / dark mode** — system-aware, persisted, toggleable from the top bar
  (and the public nav). Built on CSS-variable design tokens.
- **Search** — hybrid retrieval with optional LLM answer, top-k slider, document
  type filter, sample questions, latency/cache/freshness metrics, and per-chunk
  parent/child tabs.
- **Upload** — drag-and-drop PDF/DOCX ingestion with live job-status polling.
- **Evaluation** — runs RAGAS over the FAB test set, metric scorecards with
  pass/fail thresholds, and per-question breakdown.
- **Admin** — service readiness, reindex, and delete-collection (with confirm
  dialogs).

## Prerequisites

- **Node.js 18+** and npm (this machine does not currently have Node installed —
  grab the LTS from <https://nodejs.org>).
- The GERNAS RAG backend running on `http://localhost:8000`:

  ```bash
  # from the repository root
  uvicorn gernas_rag.main:app --reload --app-dir src
  ```

## Getting started

```bash
cd frontend
cp .env.example .env        # adjust VITE_API_KEY to match the backend API_KEY
npm install
npm run dev                 # http://localhost:5173
```

In development the Vite dev server **proxies** `/api`, `/health` and `/ready` to
`VITE_API_BASE_URL` (default `http://localhost:8000`), so the browser makes
same-origin requests and CORS never comes into play.

## Configuration

All client config lives in `.env` (Vite requires the `VITE_` prefix):

| Var                 | Default                              | Purpose                                            |
| ------------------- | ------------------------------------ | -------------------------------------------------- |
| `VITE_API_BASE_URL` | `http://localhost:8000`              | Backend base URL (dev proxy target).               |
| `VITE_API_KEY`      | `dev-secret-key-change-in-production`| Sent as `X-API-Key`. Must match backend `API_KEY`. |
| `VITE_USE_PROXY`    | `true`                               | `false` → call the backend directly (needs CORS).  |

> **Security note:** any `VITE_*` value is bundled into the client JS and visible
> in the browser. For a hardened production deployment, do not ship the API key
> to the client — terminate auth at a reverse proxy / BFF that injects the
> `X-API-Key` header server-side, and set `VITE_API_KEY=""` here.

## Scripts

```bash
npm run dev         # start dev server
npm run build       # type-check + production build to dist/
npm run preview     # preview the production build
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
```

## Production build & deploy

```bash
npm run build       # outputs to frontend/dist/
```

Serve `dist/` from any static host (Nginx, S3 + CloudFront, etc.). Configure the
host to:

1. Reverse-proxy `/api`, `/health`, `/ready` to the FastAPI backend, **or** set
   `VITE_USE_PROXY=false` + `VITE_API_BASE_URL` and rely on backend CORS.
2. Serve `index.html` for unknown routes (SPA fallback) so client-side routing
   works on refresh.

## Project structure

```
frontend/src/
├── api/            # one module per backend endpoint group
├── components/
│   ├── auth/       # ProtectedRoute
│   ├── layout/     # AppLayout, Sidebar, ApiStatus, PublicNav, AuthLayout, UserMenu
│   ├── search/     # ChunkCard, AnswerBox, SampleQuestions
│   ├── upload/     # FileDropzone
│   └── ui/         # design-system primitives (Button, Card, Alert, ThemeToggle, Logo …)
├── config/         # doc types, sample questions, RAGAS thresholds
├── contexts/       # ThemeContext (dark mode), AuthContext (mock auth)
├── hooks/          # TanStack Query hooks
├── lib/            # axios client, query client, config, utils
├── pages/          # Home, Login, Signup, Search, Upload, Evaluation, Admin
└── types/          # TypeScript mirrors of backend Pydantic models
```

## Backend endpoints used

| UI area    | Method & path                          |
| ---------- | -------------------------------------- |
| Search     | `POST /api/v1/retrieve`                |
| Upload     | `POST /api/v1/ingest`, `GET /api/v1/ingest/{job_id}` |
| Evaluation | `POST /api/v1/evaluate`, `GET /api/v1/evaluate/test-cases` |
| Admin      | `POST /api/v1/admin/reindex`, `DELETE /api/v1/admin/collection`, `GET /ready` |
| Status     | `GET /health`                          |

Keep `src/types/api.ts` in sync with `src/gernas_rag/models/*.py` if the backend
contract changes.
```
