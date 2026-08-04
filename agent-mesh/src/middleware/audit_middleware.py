import os
import re
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from src.config import Config
from src.observability import get_logger, CAT_AGENT, CAT_MCP

_log = get_logger(CAT_AGENT)
_mcp_log = get_logger(CAT_MCP)

# Maps agent name → MCP service label for metric/log attribution.
_MCP_AGENT_SERVICE: dict[str, str] = {
    "DataAgent": "datalayer",
    "RAGAgent":  "rag",
}


def _trace_ids() -> tuple[str, str]:
    """Returns (trace_id, span_id) of the active span, or ('-', '-').

    Lets each audit record correlate with the distributed trace for the same
    agent run.
    """
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if getattr(ctx, "is_valid", False):
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return "-", "-"


def get_message_text(msg: Any) -> str:
    if isinstance(msg, dict):
        return msg.get("text", "") or msg.get("content", "") or ""
    if hasattr(msg, "text") and msg.text is not None:
        return str(msg.text)
    if hasattr(msg, "content") and msg.content is not None:
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for item in c:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return " ".join(parts)
        return str(c)
    if hasattr(msg, "contents") and msg.contents is not None:
        parts = []
        for item in msg.contents:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(msg)


class AuditCallbackHandler(BaseCallbackHandler):
    """
    A LangChain callback handler that intercepts LLM calls, measures performance,
    scrubs obvious PII, and logs structured transactions to an audit log file.
    """

    def __init__(self, agent_name: str, log_path: str = None):
        super().__init__()
        self.agent_name = agent_name
        self.log_path = log_path or Config.AUDIT_LOG_FILE
        # Ensure audit log directory exists
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        # State captured at on_llm_start, consumed at on_llm_end / on_llm_error
        self._start_time: float = 0.0
        self._timestamp: str = ""
        self._scrubbed_inputs: List[str] = []
        self._session_id: str = "default_session"

    def _redact_pii(self, text: str) -> str:
        """Helper to scrub obvious PII patterns such as emails and SSNs."""
        if not text or not isinstance(text, str):
            return text

        # Redact emails
        text = re.sub(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "[REDACTED_EMAIL]",
            text,
        )
        # Redact SSNs
        text = re.sub(
            r"\d{3}-\d{2}-\d{4}",
            "[REDACTED_SSN]",
            text,
        )
        return text

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        **kwargs: Any,
    ) -> None:
        """Capture start time and input prompts before LLM execution."""
        self._start_time = time.perf_counter()
        self._timestamp = datetime.now(timezone.utc).isoformat()
        self._scrubbed_inputs = [self._redact_pii(p) for p in (prompts or [])]

        # Try to pull session_id from OTel baggage
        try:
            from opentelemetry import baggage as _baggage
            bag_sess = _baggage.get_baggage("fab.session_id")
            if bag_sess:
                self._session_id = bag_sess
        except Exception:
            pass

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Write the audit log entry after LLM execution completes."""
        end_time = time.perf_counter()
        latency_ms = int((end_time - self._start_time) * 1000)

        # Extract output text from LLMResult
        raw_output = ""
        try:
            if response.generations:
                gen = response.generations[0][0]
                if hasattr(gen, "text"):
                    raw_output = gen.text
                elif hasattr(gen, "message"):
                    raw_output = get_message_text(gen.message)
        except Exception:
            pass
        scrubbed_output = self._redact_pii(raw_output)

        trace_id, span_id = _trace_ids()

        # Pull identity from W3C baggage so audit records on remote A2A nodes
        # carry the originating user context, not just the session id.
        request_id = "-"
        baggage_user = "-"
        baggage_role = "-"
        session_id = self._session_id
        try:
            from opentelemetry import baggage as _baggage
            request_id   = _baggage.get_baggage("fab.request_id") or "-"
            baggage_user = _baggage.get_baggage("fab.user")       or "-"
            baggage_role = _baggage.get_baggage("fab.role")       or "-"
            if session_id == "default_session":
                bag_sess = _baggage.get_baggage("fab.session_id")
                if bag_sess:
                    session_id = bag_sess
        except Exception:
            pass

        # Extract token usage from LLMResult when available.
        input_tokens = output_tokens = 0
        tokens_estimated = False
        try:
            usage = (getattr(response, "llm_output", None) or {})
            token_usage = usage.get("token_usage") or usage.get("usage", {})
            if token_usage:
                input_tokens  = int(token_usage.get("prompt_tokens",     0) or 0)
                output_tokens = int(token_usage.get("completion_tokens", 0) or 0)
        except Exception:
            pass

        # Fallback: estimate from character length when usage is unavailable.
        # ~4 characters per token is a conservative estimate for English LLM text.
        if input_tokens == 0 and output_tokens == 0:
            input_text    = " ".join(self._scrubbed_inputs)
            input_tokens  = max(1, len(input_text)     // 4) if input_text     else 0
            output_tokens = max(1, len(scrubbed_output) // 4) if scrubbed_output else 0
            tokens_estimated = True

        total_tokens = input_tokens + output_tokens

        # Formulate the audit log entry (immutable compliance trail).
        log_entry = {
            "timestamp":        self._timestamp,
            "request_id":       request_id,
            "trace_id":         trace_id,
            "span_id":          span_id,
            "session_id":       session_id,
            "user":             baggage_user,
            "role":             baggage_role,
            "agent_name":       self.agent_name,
            "inputs":           self._scrubbed_inputs,
            "output":           scrubbed_output,
            "status":           "SUCCESS",
            "latency_ms":       latency_ms,
            "input_tokens":     input_tokens,
            "output_tokens":    output_tokens,
            "total_tokens":     total_tokens,
            "tokens_estimated": tokens_estimated,
        }

        # Append to JSONL audit trail file.
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            # Callback logging failures must not crash the core application flow.
            pass

        # Record MCP business metrics + log line for MCP-backed agents.
        mcp_service = _MCP_AGENT_SERVICE.get(self.agent_name)
        if mcp_service:
            try:
                from src.observability.metrics import record_mcp_call
                record_mcp_call(
                    service=mcp_service,
                    tool_name="agent_invocation",
                    result="SUCCESS",
                )
                _mcp_log.info(
                    "service=%s agent=%s status=SUCCESS latency_ms=%d req=%s",
                    mcp_service, self.agent_name, latency_ms, request_id,
                )
            except Exception:
                pass

        # Structured, trace-correlated application log line.
        try:
            _log.info(
                "agent=%s status=SUCCESS latency_ms=%d",
                self.agent_name, latency_ms,
                extra={"agent": self.agent_name, "session_id": session_id, "status": "SUCCESS"},
            )
        except Exception:
            pass

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Write the audit log entry when LLM execution fails."""
        end_time = time.perf_counter()
        latency_ms = int((end_time - self._start_time) * 1000)
        error_message = str(error)

        trace_id, span_id = _trace_ids()
        request_id = "-"
        baggage_user = "-"
        baggage_role = "-"
        session_id = self._session_id
        try:
            from opentelemetry import baggage as _baggage
            request_id   = _baggage.get_baggage("fab.request_id") or "-"
            baggage_user = _baggage.get_baggage("fab.user")       or "-"
            baggage_role = _baggage.get_baggage("fab.role")       or "-"
        except Exception:
            pass

        log_entry = {
            "timestamp":        self._timestamp,
            "request_id":       request_id,
            "trace_id":         trace_id,
            "span_id":          span_id,
            "session_id":       session_id,
            "user":             baggage_user,
            "role":             baggage_role,
            "agent_name":       self.agent_name,
            "inputs":           self._scrubbed_inputs,
            "output":           "",
            "status":           "ERROR",
            "latency_ms":       latency_ms,
            "input_tokens":     0,
            "output_tokens":    0,
            "total_tokens":     0,
            "tokens_estimated": False,
            "error":            error_message,
        }

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

        # Record MCP error metrics for MCP-backed agents.
        mcp_service = _MCP_AGENT_SERVICE.get(self.agent_name)
        if mcp_service:
            try:
                from src.observability.metrics import record_mcp_call
                record_mcp_call(
                    service=mcp_service,
                    tool_name="agent_invocation",
                    result="ERROR",
                )
                _mcp_log.info(
                    "service=%s agent=%s status=ERROR latency_ms=%d req=%s",
                    mcp_service, self.agent_name, latency_ms, request_id,
                )
            except Exception:
                pass

        try:
            _log.error(
                "agent=%s status=ERROR latency_ms=%d error=%s",
                self.agent_name, latency_ms, error_message,
                extra={"agent": self.agent_name, "session_id": session_id, "status": "ERROR"},
            )
        except Exception:
            pass
