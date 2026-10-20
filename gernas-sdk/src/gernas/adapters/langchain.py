"""LangChain / LangGraph adapter — maps callback events onto the common GERNAS model.

| LangChain / LangGraph event                | GERNAS span          |
|--------------------------------------------|----------------------|
| root runnable / compiled graph invocation  | ``agent.run``        |
| LangGraph node (``metadata.langgraph_node``)| ``agent.step``       |
| chat model / LLM call                      | ``llm.call``         |
| tool call                                  | ``tool.call``        |
| retriever call                             | ``retrieval.search`` |

Intermediate chains are plumbing and produce no span. When the runnable is invoked
inside an existing Gernas run (e.g. a ``@gernas.trace_agent`` function), that run is
reused instead of opening a second ``agent.run``.

    from gernas.adapters.langchain import instrument
    chain = instrument(prompt | llm, agent_id="faq_agent", agent_type="worker", provider="azure_openai")

Requires ``langchain-core`` (``pip install gernas-telemetry[langchain]``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas._metrics import record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import get_field, record_exception, stamp_context, stamp_outcome
from gernas.decorators.agent import AgentRun, start_run
from gernas.decorators.llm import extract_usage, record_usage
from gernas.decorators.planning import OpenStep, open_step
from gernas.decorators.retrieval import first_score, first_source
from gernas.run_state import RunState, current_run, error_category
from gernas.tracer import get_tracer, safe_set
from gernas.utils.hashing import fingerprint, payload_size

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as exc:  # pragma: no cover - only without the extra
    raise ImportError(
        "gernas.adapters.langchain requires langchain-core: pip install gernas-telemetry[langchain]"
    ) from exc

_GRAPH_BOUNDARY_NODES = ("__start__", "__end__")


@dataclass
class _Node:
    parent: UUID | None
    root: UUID
    graph_node: str | None = None
    span: Any = None  # OTel span owned by this run id (if any)
    step: OpenStep | None = None
    agent: AgentRun | None = None
    op: str | None = None
    t0: float = field(default_factory=time.perf_counter)
    state: dict = field(default_factory=dict)


class GernasCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler producing GERNAS spans (also used for LangGraph)."""

    raise_error = False  # a telemetry failure must never break the chain

    def __init__(
        self,
        agent_id: str,
        *,
        agent_type: str,
        provider: str | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
        framework: str = "langchain",
        trace_tools: bool = True,
    ) -> None:
        super().__init__()
        self.trace_tools = trace_tools
        self.agent_id, self.agent_type, self.provider = agent_id, agent_type, provider
        self.agent_name, self.agent_version, self.framework = agent_name, agent_version, framework
        self._nodes: dict[UUID, _Node] = {}
        self._runs: dict[UUID, RunState | None] = {}
        self._lock = threading.Lock()

    # ── bookkeeping ──────────────────────────────────────────────────────────
    def _register(self, run_id: UUID, parent_run_id: UUID | None, metadata: dict | None) -> _Node:
        with self._lock:
            parent = self._nodes.get(parent_run_id) if parent_run_id else None
            node = _Node(
                parent=parent_run_id if parent else None,
                root=parent.root if parent else run_id,
                graph_node=(metadata or {}).get("langgraph_node"),
            )
            self._nodes[run_id] = node
        return node

    def _parent_context(self, node: _Node) -> Any:
        with self._lock:
            pid = node.parent
            while pid is not None:
                ancestor = self._nodes.get(pid)
                if ancestor is None:
                    break
                if ancestor.span is not None:
                    return trace.set_span_in_context(ancestor.span)
                pid = ancestor.parent
        return None

    def _run(self, node: _Node) -> RunState | None:
        with self._lock:
            return self._runs.get(node.root)

    def _pop(self, run_id: UUID) -> _Node | None:
        with self._lock:
            node = self._nodes.pop(run_id, None)
            if node is not None and node.root == run_id:
                self._runs.pop(run_id, None)
            return node

    def _begin_root(self, node: _Node) -> None:
        """Bind the root callback to a run: reuse an active Gernas run, else open ``agent.run``."""
        active = current_run()
        if active is not None:  # invoked inside an existing Gernas run: reuse it
            with self._lock:
                self._runs[node.root] = active
            return
        node.agent = start_run(
            self.agent_id,
            agent_type=self.agent_type,
            name=self.agent_name,
            version=self.agent_version,
            framework=self.framework,
        )
        with self._lock:
            self._runs[node.root] = node.agent.run

    def _open(self, node: _Node, op: str, kind: SpanKind) -> Any:
        if node.parent is None:  # a model / tool / retriever invoked directly as the root
            self._begin_root(node)
        parent = trace.set_span_in_context(node.agent.span) if node.agent is not None else self._parent_context(node)
        span = get_tracer().start_span(op, context=parent, kind=kind)
        run = self._run(node)
        stamp_context(span, op, run)
        node.span, node.op = span, op
        if run is not None and op == SpanName.LLM:
            run.record_step()
        return span

    @staticmethod
    def _close(node: _Node, error: BaseException | None) -> None:
        span = node.span
        try:
            stamp_outcome(span, error)
            if error is not None and error_category(error) != "cancelled":
                record_exception(span, error)
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            elif error is None:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
            if node.agent is not None:
                node.agent.end(error)

    # ── chains / graphs / nodes ──────────────────────────────────────────────
    def on_chain_start(
        self,
        serialized: dict | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> None:
        node = self._register(run_id, parent_run_id, metadata)
        try:
            if node.parent is None:
                self._begin_root(node)
                if node.agent is not None:
                    node.span = node.agent.span
                return
            with self._lock:
                parent_graph_node = self._nodes[node.parent].graph_node if node.parent in self._nodes else None
            if (
                node.graph_node
                and node.graph_node not in _GRAPH_BOUNDARY_NODES
                and node.graph_node != parent_graph_node
            ):
                node.step = open_step(
                    node.graph_node, "graph_node", parent=self._parent_context(node), run=self._run(node)
                )
                node.span = node.step.span
        except Exception:  # noqa: BLE001
            pass

    def _end_chain(self, run_id: UUID, error: BaseException | None) -> None:
        node = self._pop(run_id)
        if node is None:
            return
        try:
            if node.agent is not None:
                node.agent.end(error)
            elif node.step is not None:
                node.step.end(error)
        except Exception:  # noqa: BLE001
            pass

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_chain(run_id, None)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_chain(run_id, error)

    # ── model calls ──────────────────────────────────────────────────────────
    def _llm_start(self, serialized: dict | None, run_id: UUID, parent_run_id: UUID | None, kwargs: dict) -> None:
        node = self._register(run_id, parent_run_id, kwargs.get("metadata"))
        try:
            params = kwargs.get("invocation_params") or {}
            meta = kwargs.get("metadata") or {}
            model = params.get("model") or params.get("model_name") or meta.get("ls_model_name")
            provider = self.provider or meta.get("ls_provider")
            span = self._open(node, SpanName.LLM, SpanKind.CLIENT)
            node.state.update(model=model, provider=provider)
            safe_set(span, Ctx.PROVIDER, provider)
            safe_set(span, Ctx.MODEL_ID, model)
        except Exception:  # noqa: BLE001
            pass

    def on_llm_start(
        self,
        serialized: dict | None,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._llm_start(serialized, run_id, parent_run_id, kwargs)

    def on_chat_model_start(
        self, serialized: dict | None, messages: list, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any
    ) -> None:
        self._llm_start(serialized, run_id, parent_run_id, kwargs)

    def _end_llm(self, run_id: UUID, response: Any, error: BaseException | None) -> None:
        node = self._pop(run_id)
        if node is None or node.span is None:
            return
        st = node.state
        try:
            if response is not None:
                usage = (
                    (response.llm_output or {}).get("token_usage")
                    if isinstance(getattr(response, "llm_output", None), dict)
                    else None
                )
                tokens = extract_usage({"usage": usage}) if usage else (None, None, None)
                generation = None
                try:
                    generation = response.generations[0][0]
                except (AttributeError, IndexError, TypeError):
                    pass
                message = getattr(generation, "message", None)
                if tokens == (None, None, None) and generation is not None:
                    tokens = extract_usage(message)
                if not st.get("model"):  # not advertised up front: take it from the response
                    model = get_field(getattr(message, "response_metadata", None), "model_name", "model") or get_field(
                        getattr(response, "llm_output", None), "model_name", "model"
                    )
                    if model:
                        st["model"] = str(model)
                        safe_set(node.span, Ctx.MODEL_ID, st["model"])
                record_usage(node.span, st.get("provider"), st.get("model"), SpanName.LLM, *tokens)
                info = getattr(generation, "generation_info", None) or {}
                safe_set(
                    node.span,
                    Ctx.STOP_REASON,
                    get_field(info, "finish_reason", "stop_reason")
                    or get_field(getattr(message, "response_metadata", None), "finish_reason", "stop_reason"),
                )
            record(
                Metric.LLM_CALLS,
                {
                    Ctx.PROVIDER: st.get("provider"),
                    Ctx.MODEL_ID: st.get("model"),
                    Ctx.OPERATION: SpanName.LLM,
                    Ctx.EXECUTION_STATUS: "error" if error else "ok",
                    Ctx.ERROR_CATEGORY: error_category(error) if error else None,
                },
                histogram=Metric.LLM_DURATION,
                duration_ms=round((time.perf_counter() - node.t0) * 1000, 3),
            )
        except Exception:  # noqa: BLE001
            pass
        self._close(node, error)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_llm(run_id, response, None)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_llm(run_id, None, error)

    # ── tools ────────────────────────────────────────────────────────────────
    def on_tool_start(
        self,
        serialized: dict | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        inputs: dict | None = None,
        **kwargs: Any,
    ) -> None:
        node = self._register(run_id, parent_run_id, kwargs.get("metadata"))
        if not self.trace_tools:
            return
        try:
            name = kwargs.get("name") or (serialized or {}).get("name") or "tool"
            span = self._open(node, SpanName.TOOL, SpanKind.CLIENT)
            payload = inputs if inputs is not None else input_str
            node.state["tool_id"] = str(name)
            safe_set(span, Ctx.TOOL_ID, str(name))
            safe_set(span, Ctx.TOOL_TYPE, "function")
            safe_set(span, Ctx.REQUEST_SIZE_BYTES, payload_size(payload))
            safe_set(span, Ctx.AUTHORIZATION_DECISION, "not_evaluated")
            run = self._run(node)
            if run is not None:
                run.on_tool_call(str(name), fingerprint(payload))
        except Exception:  # noqa: BLE001
            pass

    def _end_tool(self, run_id: UUID, output: Any, error: BaseException | None) -> None:
        node = self._pop(run_id)
        if node is None or node.span is None:
            return
        decision = "deny" if error is not None and error_category(error) == "auth" else "allow"
        safe_set(node.span, Ctx.AUTHORIZATION_DECISION, decision)
        if error is None:
            safe_set(node.span, Ctx.RESPONSE_SIZE_BYTES, payload_size(getattr(output, "content", output)))
        record(
            Metric.TOOL_CALLS,
            {
                Ctx.TOOL_ID: node.state.get("tool_id"),
                Ctx.TOOL_TYPE: "function",
                Ctx.EXECUTION_STATUS: "error" if error else "ok",
                Ctx.AUTHORIZATION_DECISION: decision,
            },
            histogram=Metric.TOOL_DURATION,
            duration_ms=round((time.perf_counter() - node.t0) * 1000, 3),
        )
        self._close(node, error)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_tool(run_id, output, None)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_tool(run_id, None, error)

    # ── retrievers ───────────────────────────────────────────────────────────
    def on_retriever_start(
        self, serialized: dict | None, query: str, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any
    ) -> None:
        node = self._register(run_id, parent_run_id, kwargs.get("metadata"))
        try:
            self._open(node, SpanName.RETRIEVAL, SpanKind.CLIENT)
        except Exception:  # noqa: BLE001
            pass

    def _end_retriever(self, run_id: UUID, documents: Any, error: BaseException | None) -> None:
        node = self._pop(run_id)
        if node is None or node.span is None:
            return
        count = len(documents) if documents is not None and hasattr(documents, "__len__") else 0
        if error is None:
            safe_set(node.span, Ctx.RESULT_COUNT, count)
            safe_set(node.span, Ctx.EMPTY_RESULT, count == 0)
            if count:
                safe_set(node.span, Ctx.RANKING_SCORE, first_score(documents[0]))
                safe_set(node.span, Ctx.SOURCE_ID, first_source(documents[0]))
        record(
            Metric.RETRIEVAL_CALLS,
            {Ctx.EXECUTION_STATUS: "error" if error else "ok", Ctx.EMPTY_RESULT: count == 0 if error is None else None},
        )
        self._close(node, error)

    def on_retriever_end(self, documents: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_retriever(run_id, documents, None)

    def on_retriever_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._end_retriever(run_id, None, error)

    def open_count(self) -> int:
        with self._lock:
            return len(self._nodes)


def instrument(
    runnable: Any,
    *,
    agent_id: str,
    agent_type: str,
    provider: str | None = None,
    agent_name: str | None = None,
    agent_version: str | None = None,
    framework: str = "langchain",
    trace_tools: bool = True,
) -> Any:
    """Bind a GERNAS callback handler to *runnable* (a LangChain runnable or LangGraph graph).

    Set ``trace_tools=False`` when the tools already carry ``@gernas.trace_tool`` (avoids duplicate spans).
    """
    handler = GernasCallbackHandler(
        agent_id,
        agent_type=agent_type,
        provider=provider,
        agent_name=agent_name,
        agent_version=agent_version,
        framework=framework,
        trace_tools=trace_tools,
    )
    return runnable.with_config(callbacks=[handler])
