import os
import sys
import shutil
import json
import asyncio
import re
import unittest
from typing import Any, List, Dict, Optional
from unittest.mock import patch

from agent_framework import Message, Content, ResponseStream, ChatResponseUpdate
from src.config import Config
from src.memory.session_store import FileSessionStore
from src.agents.compliance_agent import get_compliance_agent
from src.agents.policy_retrieval_agent import get_policy_agent
from src.agents.coordinator_agent import run_multi_agent_workflow
from src.agents.mesh_workflow import workflow

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

class LocalMockMessage:
    def __init__(self, content: str, role: str = "assistant"):
        self.content = content
        self.role = role
        self.text = content
        self.author_name = None
        self.content_type = None
        
    def __str__(self):
        return self.content

class LocalMockChoice:
    def __init__(self, content: str):
        self.message = LocalMockMessage(content)
        self.finish_reason = "stop"

class LocalMockChatResponse:
    def __init__(self, content: str):
        import time
        self.message = LocalMockMessage(content)
        self.text = content
        self.choices = [LocalMockChoice(content)]
        self.messages = [self.message]
        self.response_id = "mock-response-id"
        self.model_id = "mock-model"
        self.created_at = int(time.time())

    def __getattr__(self, name: str) -> Any:
        return None

class LocalMockChatClient:
    def __init__(self, model: str = "mock-model", **kwargs: Any):
        self.model = model
        self.kwargs = kwargs

    async def _get_response_impl(
        self, 
        messages: List[Any], 
        options: Optional[Dict[str, Any]] = None,
        *args: Any,
        **kwargs: Any
    ) -> LocalMockChatResponse:
        system_instructions = ""
        if options and isinstance(options, dict) and "instructions" in options:
            system_instructions = options["instructions"] or ""

        user_queries = []
        full_conversation = []

        for msg in messages:
            role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
            text = get_message_text(msg)
            
            if role == "system":
                system_instructions += text + "\n"
            elif role == "user":
                user_queries.append(text)
                full_conversation.append(f"User: {text}")
            elif role == "assistant":
                full_conversation.append(f"Assistant: {text}")

        last_query = user_queries[-1] if user_queries else ""
        system_lower = system_instructions.lower()
        query_lower = last_query.lower()
        
        if "coordinator" in system_lower:
            conv_str = "\n".join(full_conversation).lower()
            if "action_success" in conv_str:
                return LocalMockChatResponse(
                    "Workflow completed successfully. The action was approved and executed. "
                    "Access to the finance folder has been granted or the reimbursement has been scheduled and queued."
                )
            elif "denied" in conv_str:
                return LocalMockChatResponse(
                    "Workflow stopped. The request was rejected by the manager or failed compliance checks."
                )
            
            if "finance" in query_lower or "folder" in query_lower:
                return LocalMockChatResponse(
                    "ROUTE: Routing this access request to the Compliance Agent for verification."
                )
            elif "reimbursement" in query_lower or "travel" in query_lower:
                return LocalMockChatResponse(
                    "ROUTE: Routing this travel expense / policy query to the Policy Retrieval Agent."
                )
            else:
                return LocalMockChatResponse(
                    "ROUTE: Routing this inquiry to the Policy Retrieval Agent for standard guidelines."
                )

        elif "policy retrieval agent" in system_lower:
            if "finance" in query_lower or "folder" in query_lower:
                return LocalMockChatResponse(
                    "POLICY_INFO: Access to the Finance Folder is restricted. Rules: Requires Compliance Check "
                    "and Manager Approval. Duration limit is 90 days."
                )
            elif "reimbursement" in query_lower or "travel" in query_lower:
                amounts = re.findall(r"\$?(\d+(?:\.\d+)?)", query_lower)
                amount = float(amounts[0]) if amounts else 0.0
                
                if amount >= 500.0:
                    return LocalMockChatResponse(
                        f"POLICY_INFO: Travel reimbursement request for ${amount:.2f} exceeds the pre-approval limit of $500.00. "
                        "Rules: Requires Manager Approval."
                    )
                else:
                    return LocalMockChatResponse(
                        f"POLICY_INFO: Travel reimbursement request for ${amount:.2f} is within the pre-approval limit of $500.00. "
                        "Rules: Pre-approved, no additional approval required."
                    )
            else:
                return LocalMockChatResponse(
                    "POLICY_INFO: General corporate policy allows employees to make standard IT requests. "
                    "No formal approval gate is required."
                )

        elif "compliance agent" in system_lower:
            has_email = bool(re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", last_query))
            has_ssn = bool(re.search(r"\d{3}-\d{2}-\d{4}", last_query))
            
            if has_email or has_ssn:
                return LocalMockChatResponse(
                    "COMPLIANCE_FAILED: The request contains sensitive PII (redacted). Execution halted."
                )
            else:
                return LocalMockChatResponse(
                    "COMPLIANCE_PASSED: The request complies with safety policies. No PII detected."
                )

        elif "approval gate agent" in system_lower:
            conv_str = "\n".join(full_conversation).lower()
            needs_approval = "requires manager approval" in conv_str or "restricted" in conv_str
            
            if not needs_approval:
                return LocalMockChatResponse("APPROVED: Auto-approved as no approval policy applies.")
            
            return LocalMockChatResponse("APPROVED: Manager approval has been granted (Auto-approved in non-interactive terminal).")

        elif "action / execution agent" in system_lower:
            conv_str = "\n".join(full_conversation).lower()
            if "denied" in conv_str or "failed" in conv_str:
                return LocalMockChatResponse("ACTION_FAILED: Request cannot be executed because safety or approval checks failed.")
            
            if "folder" in query_lower or "finance" in query_lower:
                return LocalMockChatResponse("ACTION_SUCCESS: Finance folder read/write access has been provisioned for 90 days.")
            elif "reimbursement" in query_lower or "travel" in query_lower:
                return LocalMockChatResponse("ACTION_SUCCESS: Expense reimbursement payout has been scheduled.")
            else:
                return LocalMockChatResponse("ACTION_SUCCESS: Standard request fulfilled successfully.")

        return LocalMockChatResponse(f"Echo (Mock Mode): I received your request for: '{last_query}'")

    def get_response(
        self, 
        messages: List[Any], 
        *,
        stream: bool = False,
        options: Optional[Dict[str, Any]] = None,
        **kwargs: Any
    ) -> Any:
        if stream:
            async def _stream_impl():
                response_obj = await self._get_response_impl(messages, options, **kwargs)
                yield ChatResponseUpdate(
                    contents=[Content.from_text(text=response_obj.text)],
                    role="assistant",
                    response_id="mock-response-id",
                    model="mock-model"
                )
            return ResponseStream(_stream_impl())
        else:
            return self._get_response_impl(messages, options, **kwargs)

class TestMultiAgentSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure env variables for sandboxed test execution
        Config.AUDIT_LOG_FILE = "data/audit_trail_test.jsonl"
        Config.CONVERSATION_STORE_DIR = "data/conversations_test"
        
        # Ensure a clean state for test artifacts
        if os.path.exists("data/conversations_test"):
            shutil.rmtree("data/conversations_test")
        if os.path.exists("data/audit_trail_test.jsonl"):
            os.remove("data/audit_trail_test.jsonl")

        # Start patching the OllamaChatClient directly in agent_factory module
        cls.patcher = patch("src.agents.agent_factory.OllamaChatClient", new=LocalMockChatClient)
        cls.patcher.start()

    @classmethod
    def tearDownClass(cls):
        # Stop patcher
        cls.patcher.stop()

        # Clean up test files after suite runs
        if os.path.exists("data/conversations_test"):
            shutil.rmtree("data/conversations_test")
        if os.path.exists("data/audit_trail_test.jsonl"):
            try:
                os.remove("data/audit_trail_test.jsonl")
            except PermissionError:
                pass

    def setUp(self):
        # Create a new event loop for this test run
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_compliance_safety_check(self):
        """Verifies that the ComplianceAgent flags PII and approves clean requests."""
        compliance_agent = get_compliance_agent(log_path="data/audit_trail_test.jsonl")
        
        # Safe input
        res = self.loop.run_until_complete(compliance_agent.run("Requesting standard folder access"))
        text = getattr(res, "text", str(res))
        self.assertIn("COMPLIANCE_PASSED", text)
        
        # Unsafe input containing email
        res = self.loop.run_until_complete(compliance_agent.run("Contact me at user@corp.com to approve finance folder access"))
        text = getattr(res, "text", str(res))
        self.assertIn("COMPLIANCE_FAILED", text)
        
        # Unsafe input containing SSN
        res = self.loop.run_until_complete(compliance_agent.run("SSN is 999-12-3456. Grant permission please."))
        text = getattr(res, "text", str(res))
        self.assertIn("COMPLIANCE_FAILED", text)

    def test_policy_retrieval_limits(self):
        """Verifies that the PolicyRetrievalAgent parses reimbursement amounts and looks up policy rules."""
        policy_agent = get_policy_agent(log_path="data/audit_trail_test.jsonl")
        
        # Test low-risk limit ($250)
        res = self.loop.run_until_complete(policy_agent.run("Travel reimbursement for $250"))
        text = getattr(res, "text", str(res))
        self.assertIn("pre-approved", text.lower())
        self.assertIn("no additional approval required", text.lower())
        
        # Test high-risk limit ($600)
        res = self.loop.run_until_complete(policy_agent.run("Travel reimbursement for $600"))
        text = getattr(res, "text", str(res))
        self.assertIn("requires manager approval", text.lower())

    def test_end_to_end_pre_approved_workflow(self):
        """Verifies that a low-risk pre-approved query completes successfully in one workflow pass."""
        session_id = "session_test_under_limit"
        summary = self.loop.run_until_complete(
            run_multi_agent_workflow(
                user_query="Submit travel reimbursement for $200", 
                session_id=session_id, 
                log_path="data/audit_trail_test.jsonl"
            )
        )
        self.assertIn("scheduled", summary.lower())
        self.assertNotIn("denied", summary.lower())
        
        # Check that thread memory files were generated
        store = FileSessionStore(storage_dir="data/conversations_test")
        history = store.load_session(session_id)
        self.assertTrue(len(history) > 0)
        
        # Check that audit log file was populated
        self.assertTrue(os.path.exists("data/audit_trail_test.jsonl"))

    def test_audit_log_pii_redaction(self):
        """Verifies that clear PII is redacted in the audit log files before saving."""
        session_id = "session_test_pii"
        self.loop.run_until_complete(
            run_multi_agent_workflow(
                user_query="Request folder access. Contact admin@domain.com, SSN 111-22-3333.", 
                session_id=session_id, 
                log_path="data/audit_trail_test.jsonl"
            )
        )
        
        # Verify the audit log file contains the redacted placeholder and does not leak the actual values
        self.assertTrue(os.path.exists("data/audit_trail_test.jsonl"))
        with open("data/audit_trail_test.jsonl", "r", encoding="utf-8") as f:
            logs = f.read()
            
        self.assertIn("[REDACTED_EMAIL]", logs)
        self.assertIn("[REDACTED_SSN]", logs)
        self.assertNotIn("admin@domain.com", logs)
        self.assertNotIn("111-22-3333", logs)

    def test_declarative_workflow_execution(self):
        """Verifies that the MultiAgentMeshWorkflow runs successfully via WorkflowBuilder."""
        events = self.loop.run_until_complete(workflow.run("Submit travel reimbursement for $200"))
        outputs = events.get_outputs()
        self.assertTrue(len(outputs) > 0)
        self.assertIn("scheduled", outputs[0].lower())

if __name__ == "__main__":
    unittest.main()
