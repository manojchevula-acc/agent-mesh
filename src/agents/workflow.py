import sys
import pathlib
import uuid

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import Never
from agent_framework import Executor, handler, WorkflowContext, WorkflowBuilder
from src.agents.coordinator import run_multi_agent_workflow

class MultiAgentMeshExecutor(Executor):
    """
    Custom Executor that wraps and runs the procedural multi-agent mesh orchestration.
    This exposes the complete coordinated agent loop (Compliance, Policy, Approvals, Action)
    to the Microsoft Agent Framework Workflow engine so it can be executed inside DevUI.
    """
    @handler
    async def process(self, user_query: str, ctx: WorkflowContext[Never, str]) -> None:
        session_id = f"workflow_{uuid.uuid4().hex[:8]}"
        final_summary = await run_multi_agent_workflow(
            user_query=user_query,
            session_id=session_id
        )
        await ctx.yield_output(final_summary)

# Instantiate the start executor
mesh_executor = MultiAgentMeshExecutor(id="mesh_executor")

# Build the declarative workflow
workflow = (
    WorkflowBuilder(
        name="MultiAgentMeshWorkflow",
        description="Cooperative multi-agent mesh workflow orchestrating Compliance, Policy, Approvals, and Execution.",
        start_executor=mesh_executor,
        output_from=[mesh_executor]
    )
    .build()
)
