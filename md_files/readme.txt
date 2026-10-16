Good question — and the honest answer is: **the brief is about 70% there for Claude Code, but needs four specific adaptations before it will reliably work**. Here's the full picture.

---

## 1. What the Brief Gets Right

The gate protocol, non-negotiable principles, attribute naming, decorator list, and acceptance criteria format are all exactly what Claude Code needs. That content will anchor it well and prevent the most common failure modes (inventing architecture, using auto-instrumentation packages, ignoring PDPL).

---

## 2. What's Missing or Needs Fixing for Claude Code

### Problem 1 — Claude Code reads `CLAUDE.md`, not arbitrary markdown

Claude Code automatically loads `CLAUDE.md` from the repo root into every context window. Your developer brief is a `docs/` reference file, which is correct — but you also need a lean `CLAUDE.md` that Claude Code will actually see. The full brief is too long to be `CLAUDE.md` directly (it'll crowd out code context).

**Fix:** Two-file split:

```
repo-root/
  CLAUDE.md                    ← ~600 words, core rules only, read by Claude Code automatically
  docs/DEVELOPER_BRIEF.md      ← full brief (what you just created)
  docs/SPRINT_PLAN.md          ← the v2.md sprint plan (already exists)
  docs/BRIEFING_NOTES.md       ← extracted key sub-task specs from the PDF
  IMPLEMENTATION_PLAN.md       ← pre-populated task list (see below)
  IMPLEMENTATION_LOG.md        ← empty template, Claude Code fills it
```

### Problem 2 — Claude Code will ignore "stop" unless the instruction is in every prompt

CLAUDE.md tells it the principle once. But when you actually give Claude Code a task in the terminal, you need to repeat the stop instruction in that prompt too. Claude Code in autonomous mode will keep going otherwise.

**Fix:** Every task prompt to Claude Code ends with:
```
Implement [Task ID] Step [N] only. After running tests and updating IMPLEMENTATION_LOG.md, 
print a completion report and stop. Do not proceed to the next step.
```

### Problem 3 — IMPLEMENTATION_PLAN.md doesn't exist yet and Claude Code shouldn't derive it

If you ask Claude Code to create the implementation plan from the sprint plan, it will invent tasks, reorder things, and miss the acceptance criteria. The plan should be pre-populated by you (or by this session) so Claude Code is executing against a known structure, not authoring it.

**Fix:** I can generate the full `IMPLEMENTATION_PLAN.md` from the sprint plan content. You'd review and approve it, then Claude Code works from it.

### Problem 4 — No `pyproject.toml` or repo structure exists yet

Claude Code needs to know the package shape before it writes any code. Without this, it'll make up a structure that may not match the SOW package name, test conventions, or CI expectations.

---

## 3. Python Dependencies

```toml
[project]
name = "gernas-telemetry"
version = "0.1.0"
description = "GERNAS Agent Telemetry SDK — WP1"
requires-python = ">=3.11"
dependencies = [
    "opentelemetry-sdk>=1.24.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.24.0",
    "opentelemetry-exporter-otlp-proto-http>=1.24.0",
    "opentelemetry-semantic-conventions>=0.45b0",   # needed for gen_ai.* conventions
]

[project.optional-dependencies]
langchain = [
    "langchain>=0.2.0",
    "langchain-core>=0.2.0",
]
opik = ["opik>=1.0.0"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",   # needed for async decorator tests
    "pytest-cov>=5.0",
    "mypy>=1.8",
    "ruff>=0.4",              # lint + the conformance rule will live here
]
```

**Critical:** Do not add `opentelemetry-instrumentation-openai`, `opentelemetry-instrumentation-anthropic`, or any `opentelemetry-instrumentation-*` package. The non-negotiable engineering principle (build from primitives) means these packages must not appear in the dependency tree at all — Claude Code will happily reach for them if you don't block this explicitly.

The `opentelemetry-semantic-conventions` version matters. The `gen_ai.*` attribute namespace (provider, model, token counts) was added in the `0.45b0` series — pin at or above that.

---

## 4. How to Actually Apply This — Step by Step

### Step A — Set up the repo structure first (do this manually, not via Claude Code)

```
gernas-telemetry/
  CLAUDE.md                          ← lean rules file (see Step B)
  pyproject.toml                     ← dependencies above
  IMPLEMENTATION_PLAN.md             ← pre-populated (I can generate this)
  IMPLEMENTATION_LOG.md              ← empty template
  gernas/
    __init__.py
    config.py
    tracer.py
    decorators.py
    attributes.py
    processors.py
    exporters.py
  tests/
    conftest.py
    test_core.py
  docs/
    DEVELOPER_BRIEF.md               ← the full brief you just created
    SPRINT_PLAN.md                   ← the v2.md file
    BRIEFING_NOTES.md                ← key sub-task specs
  collector/
    otel-collector-config.yaml       ← WP2 lives here
  dashboards/                        ← WP3 React app goes here
```

### Step B — Write a lean `CLAUDE.md` (the actual Claude Code instruction file)

This should be ~600 words max. The full brief lives in `docs/`. CLAUDE.md covers only:
- What this project is (2 sentences)
- The 8 non-negotiable engineering principles (bullet list, already in the brief — copy them verbatim)
- The gate protocol ("implement one step, run tests, stop, report, wait")
- What NOT to do (no auto-instrumentation packages, no hardcoded values, no vendor imports in core)
- Where to find reference files (`docs/DEVELOPER_BRIEF.md`, `IMPLEMENTATION_PLAN.md`)

### Step C — First Claude Code session

Don't hand it everything at once. Start with this exact prompt:

```
Read CLAUDE.md, IMPLEMENTATION_PLAN.md, and the existing files in gernas/.
Tell me your understanding of the project and the first implementation task. 
Do not write any code yet.
```

Review what it tells you. Correct any misunderstandings. Then:

```
Implement TASK-WP1-CORE-01, Step 1 only: TracerProvider setup and single-call 
initialisation in gernas/tracer.py and gernas/config.py. 
Run tests after implementation, update IMPLEMENTATION_LOG.md, print a completion 
report, and stop. Do not proceed to Step 2.
```

### Step D — Collector and dashboard sessions (WP2, WP3)

These are separate workstreams. Keep them as separate Claude Code sessions or separate prompts with clear scope. Don't mix SDK work and Collector config in the same session — Claude Code will conflate them.

---

## 5. The Biggest Practical Risk with Claude Code on This Project

**The gate protocol will fail without active discipline on your side.** Claude Code is trained to be helpful and complete tasks. It will interpret "implement the streaming decorator" as "implement it end to end including tests and integration" — which is more than one step. You need to be specific:

```
Implement @trace_stream Step 2 only: TTFT measurement on first chunk.
Modify gernas/decorators.py only. Run pytest tests/test_trace_stream.py.
Stop after printing the test result.
```

Not:
```
Implement @trace_stream
```

The second prompt will produce a complete but unreviewed implementation that skips the architectural checkpoints the gate protocol exists to catch.

---

## 6. What I'd Suggest You Let Me Generate Before Starting

Three things that are better produced here (where the full context is loaded) than derived by Claude Code from scratch:

| Artefact | Why generate here |
|---|---|
| `IMPLEMENTATION_PLAN.md` | Needs to correctly map sprint tasks to steps with the right acceptance criteria from the briefing notes |
| `CLAUDE.md` | Needs to be exactly the right length and reference the right things without padding |
| `IMPLEMENTATION_LOG.md` template | Needs the right structure so Claude Code's entries are consistent across all 13 sprints |

Want me to produce those three now? The `IMPLEMENTATION_PLAN.md` in particular — I can generate it covering WP1 fully (since that's BE1's first six sprints) with the exact acceptance criteria from the briefing notes wired in, so Claude Code has no room to invent its own definition of done.