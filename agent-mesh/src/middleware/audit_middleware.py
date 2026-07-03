import os
import re
import json
import time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any, List, Dict

from agent_framework import AgentMiddleware, AgentContext
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

    Lets each audit record correlate with the distributed trace emitted by the
    SDK's AgentTelemetryLayer for the same agent run.
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


class AuditMiddleware(AgentMiddleware):
    """
    An AgentMiddleware that intercepts agent invocations, measures performance,
    scrubs obvious PII, and logs structured transactions to an audit log file.
    """
    def __init__(self, log_path: str = None):
        self.log_path = log_path or Config.AUDIT_LOG_FILE
        # Ensure audit log directory exists
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    def _redact_pii(self, text: str) -> str:
        """Helper to scrub obvious PII patterns such as emails and SSNs."""
        if not text or not isinstance(text, str):
            return text
        
        # Redact emails
        text = re.sub(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", 
            "[REDACTED_EMAIL]", 
            text
        )
        # Redact SSNs
        text = re.sub(
            r"\d{3}-\d{2}-\d{4}", 
            "[REDACTED_SSN]", 
            text
        )
        return text

    async def process(
        self, 
        context: AgentContext, 
        call_next: Callable[[], Awaitable[None]]
    ) -> None:
        """Intercepts agent execution, measures latency, redacts PII, and writes to JSONL."""
        start_time = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # 1. Capture inputs prior to execution
        agent_name = getattr(context.agent, "name", "unknown_agent")
        
        # Extract and scrub input messages
        raw_inputs = []
        if hasattr(context, "messages") and context.messages:
            raw_inputs = [get_message_text(m) for m in context.messages]
        elif hasattr(context, "input") and context.input:
            raw_inputs = [str(context.input)]
            
        scrubbed_inputs = [self._redact_pii(inp) for inp in raw_inputs]
        session_id = getattr(context.session, "id", "default_session") if hasattr(context, "session") and context.session else "default_session"
        
        status = "SUCCESS"
        error_message = None
        
        try:
            # 2. Invoke the next middleware or agent executor
            await call_next()
        except Exception as e:
            status = "ERROR"
            error_message = str(e)
            raise e
        finally:
            # 3. Process outputs after execution
            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            
            # Extract and scrub output response
            raw_output = ""
            if hasattr(context, "result") and context.result:
                # Handle agent response representation
                if hasattr(context.result, "text"):
                    raw_output = context.result.text
                elif hasattr(context.result, "message") and hasattr(context.result.message, "content"):
                    raw_output = context.result.message.content
                else:
                    raw_output = str(context.result)
                    
            scrubbed_output = self._redact_pii(raw_output)
            trace_id, span_id = _trace_ids()

            # Pull identity from W3C baggage so audit records on remote A2A nodes
            # carry the originating user context, not just the session id.
            request_id = "-"
            baggage_user = "-"
            baggage_role = "-"
            try:
                from opentelemetry import baggage as _baggage
                request_id  = _baggage.get_baggage("fab.request_id") or "-"
                baggage_user = _baggage.get_baggage("fab.user")      or "-"
                baggage_role = _baggage.get_baggage("fab.role")      or "-"
                if session_id == "default_session":
                    bag_sess = _baggage.get_baggage("fab.session_id")
                    if bag_sess:
                        session_id = bag_sess
            except Exception:
                pass

            # 4. Formulate the audit log entry (immutable compliance trail).
            #    Correlated with the SDK's invoke_agent span via trace/span ids.
            log_entry = {
                "timestamp":  timestamp,
                "request_id": request_id,
                "trace_id":   trace_id,
                "span_id":    span_id,
                "session_id": session_id,
                "user":       baggage_user,
                "role":       baggage_role,
                "agent_name": agent_name,
                "inputs":     scrubbed_inputs,
                "output":     scrubbed_output,
                "status":     status,
                "latency_ms": latency_ms,
            }
            if error_message:
                log_entry["error"] = error_message

            # 5. Append to JSONL audit trail file (audit, not telemetry: the SDK's
            #    AgentTelemetryLayer owns the agent span, so we do NOT emit one here).
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            except Exception:
                # Middleware logging failures should not crash the core application flow
                pass

            # 5b. Record MCP business metrics + log line for MCP-backed agents.
            #     The Agent Framework emits an execute_tool span per tool call (visible in
            #     Tempo), but our custom fab.mcp.calls.total counter and mesh.mcp log
            #     need a separate hook. AuditMiddleware runs per agent invocation which
            #     gives us service-level granularity (datalayer / rag).
            mcp_service = _MCP_AGENT_SERVICE.get(agent_name)
            if mcp_service:
                try:
                    from src.observability.metrics import record_mcp_call
                    record_mcp_call(
                        service=mcp_service,
                        tool_name="agent_invocation",
                        result=status,
                    )
                    _mcp_log.info(
                        "service=%s agent=%s status=%s latency_ms=%d req=%s",
                        mcp_service, agent_name, status, latency_ms, request_id,
                    )
                except Exception:
                    pass

            # 5c. Token tracking safety net — try to extract exact token counts from
            #     the framework response object. Runs in the remote agent node process,
            #     so it records to OTel (Grafana) only; the in-process accumulator in
            #     api_server is populated separately by ask_remote() (approximation).
            if status == "SUCCESS":
                try:
                    from src.observability.metrics import record_llm_tokens
                    usage = None
                    if hasattr(context, "result") and context.result:
                        usage = (
                            getattr(context.result, "usage", None)
                            or getattr(context.result, "token_usage", None)
                        )
                    if usage:
                        input_tok = (
                            getattr(usage, "prompt_tokens", 0)
                            or getattr(usage, "input_tokens", 0)
                            or 0
                        )
                        output_tok = (
                            getattr(usage, "completion_tokens", 0)
                            or getattr(usage, "output_tokens", 0)
                            or 0
                        )
                        if input_tok or output_tok:
                            _NAME_TO_MODEL = {
                                "ComplianceAgent":  Config.COMPLIANCE_MODEL,
                                "DataAgent":        Config.DATA_AGENT_MODEL,
                                "RAGAgent":         Config.RAG_AGENT_MODEL,
                                "PriceAssistAgent": Config.PRICE_ASSIST_MODEL,
                            }
                            record_llm_tokens(
                                agent_name=agent_name,
                                model=_NAME_TO_MODEL.get(agent_name, Config.GROQ_MODEL),
                                role=baggage_role,
                                input_tokens=int(input_tok),
                                output_tokens=int(output_tok),
                                session_id=session_id,
                            )
                except Exception:
                    pass

            # 6. Structured, trace-correlated application log line.
            try:
                if status == "ERROR":
                    _log.error("agent=%s status=ERROR latency_ms=%d error=%s",
                               agent_name, latency_ms, error_message,
                               extra={"agent": agent_name, "session_id": session_id,
                                      "status": status})
                else:
                    _log.info("agent=%s status=%s latency_ms=%d",
                              agent_name, status, latency_ms,
                              extra={"agent": agent_name, "session_id": session_id,
                                     "status": status})
            except Exception:
                pass
