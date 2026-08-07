import math
import os
import sys
import pathlib
import statistics as stats_module

# Load .env files BEFORE importing auth — auth.py reads MCP_API_KEY and
# MCP_JWT_SECRET at module level, so they must be in os.environ first.
try:
    from dotenv import load_dotenv as _load_dotenv
    _here = pathlib.Path(__file__).resolve().parent
    _load_dotenv(_here.parent.parent / ".env")          # project root .env (MCP auth keys)
    _load_dotenv(_here.parent / ".env", override=True)  # datalayer-as-service/.env (MySQL creds)
except ImportError:
    pass

import uvicorn
from fastmcp import FastMCP

try:
    from auth import mcp_middleware, MCP_AUTH_ENABLED, require_role, audit_log
except ImportError:
    from mcp_server.auth import mcp_middleware, MCP_AUTH_ENABLED, require_role, audit_log

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8002

mcp = FastMCP("Mathematical Calculator Service")

SAFE_MATH = {
    "__builtins__": {},
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "pi": math.pi, "e": math.e,
    "factorial": math.factorial, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil, "pow": pow,
}

@mcp.tool()
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression.

    Args:
        expression: Mathematical expression e.g. "15!", "sqrt(144)", "2**10", "sin(pi/2)", "factorial(10)"

    Returns:
        Result of the calculation
    """
    require_role("admin", "agent")
    audit_log("calculate", {"expression": expression}, service="compute")
    original = expression.strip()
    # Handle factorial shorthand: 15! -> factorial(15)
    if original.endswith("!") and original[:-1].strip().isdigit():
        n = int(original[:-1].strip())
        result = math.factorial(n)
        return f"{original} = {result:,}"

    # Replace ^ with ** for exponentiation
    expr = original.replace("^", "**")

    try:
        result = eval(expr, SAFE_MATH)
        if isinstance(result, float):
            return f"{original} = {result:.6g}"
        return f"{original} = {result:,}" if isinstance(result, int) else f"{original} = {result}"
    except Exception as ex:
        return f"Error evaluating '{original}': {ex}"

@mcp.tool()
def statistics_summary(numbers: str) -> str:
    """Calculate statistical measures for a list of numbers.

    Args:
        numbers: Comma-separated list of numbers e.g. "12, 45, 67, 23, 89"

    Returns:
        Statistical summary: count, mean, median, std dev, min, max, sum
    """
    require_role("admin", "agent")
    audit_log("statistics_summary", {"numbers": numbers}, service="compute")
    try:
        nums = [float(x.strip()) for x in numbers.split(",") if x.strip()]
        if not nums:
            return "No numbers provided"
        mean_val = stats_module.mean(nums)
        median_val = stats_module.median(nums)
        std_val = stats_module.stdev(nums) if len(nums) > 1 else 0.0
        return (
            f"Statistics for [{numbers}]:\n"
            f"  Count:   {len(nums)}\n"
            f"  Mean:    {mean_val:.4f}\n"
            f"  Median:  {median_val:.4f}\n"
            f"  Std Dev: {std_val:.4f}\n"
            f"  Min:     {min(nums)}\n"
            f"  Max:     {max(nums)}\n"
            f"  Sum:     {sum(nums)}"
        )
    except ValueError as e:
        return f"Error parsing numbers: {e}"

@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between units of measurement.

    Args:
        value: Numeric value to convert
        from_unit: Source unit (km, miles, celsius, fahrenheit, kg, pounds, meters, feet, liters, gallons)
        to_unit: Target unit

    Returns:
        Converted value with units
    """
    require_role("admin", "agent")
    audit_log("convert_units", {"from_unit": from_unit, "to_unit": to_unit}, service="compute")
    conversions = {
        ("km", "miles"):         lambda x: x * 0.621371,
        ("miles", "km"):         lambda x: x * 1.60934,
        ("celsius", "fahrenheit"): lambda x: x * 9 / 5 + 32,
        ("fahrenheit", "celsius"): lambda x: (x - 32) * 5 / 9,
        ("kg", "pounds"):        lambda x: x * 2.20462,
        ("pounds", "kg"):        lambda x: x * 0.453592,
        ("meters", "feet"):      lambda x: x * 3.28084,
        ("feet", "meters"):      lambda x: x * 0.3048,
        ("liters", "gallons"):   lambda x: x * 0.264172,
        ("gallons", "liters"):   lambda x: x * 3.78541,
        ("inches", "cm"):        lambda x: x * 2.54,
        ("cm", "inches"):        lambda x: x / 2.54,
    }
    key = (from_unit.lower(), to_unit.lower())
    if key in conversions:
        result = conversions[key](value)
        return f"{value} {from_unit} = {result:.4f} {to_unit}"
    supported = ", ".join(f"{a}->{b}" for a, b in conversions.keys())
    return f"Unsupported conversion: {from_unit} to {to_unit}\nSupported: {supported}"

if __name__ == "__main__":
    auth_msg = f"enabled ({os.environ.get('MCP_AUTH_PROVIDER', 'local')} provider)" if MCP_AUTH_ENABLED else "disabled"
    print(f"Starting Calculator MCP Server on port {PORT}...")
    print(f"SSE endpoint : http://localhost:{PORT}/sse")
    print(f"Auth         : {auth_msg}")
    app = mcp.http_app(transport="sse", middleware=mcp_middleware())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
