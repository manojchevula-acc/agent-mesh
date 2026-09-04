"""The tool decorator, in one place.

Every tool module imports `tool` from here rather than from a framework, so swapping
the orchestrator again costs one line instead of eleven. MAF's @tool derives
the JSON schema the same way LangChain's @tool did: the function signature gives the
parameters, the docstring gives the description the model sees. Every existing tool
docstring therefore carries over verbatim — and those docstrings ARE the routing
logic (Design §3.4), so this is the property that matters most.
"""

from agent_framework import tool as _maf_tool

# MAF's decorator is already called `tool` and produces a FunctionTool whose
# .name/.description/.func/.input_model this codebase relies on. Re-exported under
# the same name so the 11 tool modules only change their import line.
tool = _maf_tool

__all__ = ["tool"]
