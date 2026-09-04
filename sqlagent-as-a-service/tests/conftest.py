"""Shared test helpers."""


def call_tool(fn, args: dict):
    """Invoke a decorated tool the way the agent does — schema validation then the
    underlying callable. The test-facing stand-in for StructuredTool.invoke(dict)."""
    return fn.func(**fn.input_model(**args).model_dump())
