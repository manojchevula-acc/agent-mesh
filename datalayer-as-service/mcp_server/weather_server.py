import os
import pathlib
import random
import sys
from datetime import datetime, timedelta

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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8001

mcp = FastMCP("Weather Information Service")

MOCK_WEATHER = {
    "london":   {"temp": 15, "condition": "Cloudy with light rain", "humidity": 78, "wind": "12 km/h SW"},
    "paris":    {"temp": 22, "condition": "Partly cloudy",          "humidity": 65, "wind": "8 km/h NW"},
    "new york": {"temp": 28, "condition": "Sunny",                  "humidity": 55, "wind": "15 km/h NE"},
    "tokyo":    {"temp": 30, "condition": "Humid and partly sunny", "humidity": 82, "wind": "10 km/h E"},
    "sydney":   {"temp": 18, "condition": "Clear skies",            "humidity": 60, "wind": "20 km/h SE"},
    "dubai":    {"temp": 42, "condition": "Hot and sunny",          "humidity": 45, "wind": "5 km/h NW"},
    "berlin":   {"temp": 18, "condition": "Overcast",               "humidity": 70, "wind": "14 km/h W"},
    "mumbai":   {"temp": 34, "condition": "Humid and sunny",        "humidity": 88, "wind": "18 km/h SW"},
    "singapore":{"temp": 32, "condition": "Tropical showers",       "humidity": 85, "wind": "8 km/h SW"},
    "chicago":  {"temp": 24, "condition": "Windy and sunny",        "humidity": 52, "wind": "30 km/h NW"},
}

@mcp.tool()
def get_current_weather(city: str) -> str:
    """Get the current weather conditions for a city.

    Args:
        city: Name of the city to get weather for

    Returns:
        Current weather conditions including temperature, humidity, and wind
    """
    require_role("admin", "agent")
    audit_log("get_current_weather", {"city": city}, service="mock")
    data = MOCK_WEATHER.get(city.lower())
    if data:
        return (
            f"Current weather in {city.title()}:\n"
            f"  Temperature: {data['temp']}°C ({data['temp'] * 9//5 + 32}°F)\n"
            f"  Condition: {data['condition']}\n"
            f"  Humidity: {data['humidity']}%\n"
            f"  Wind: {data['wind']}"
        )
    temp = random.randint(10, 35)
    return (
        f"Current weather in {city.title()}:\n"
        f"  Temperature: {temp}°C ({temp * 9//5 + 32}°F)\n"
        f"  Condition: Clear skies\n"
        f"  Humidity: 65%\n"
        f"  Wind: 10 km/h NW"
    )

@mcp.tool()
def get_forecast(city: str, days: int = 5) -> str:
    """Get weather forecast for upcoming days.

    Args:
        city: Name of the city
        days: Number of forecast days (1-7, default 5)

    Returns:
        Multi-day weather forecast
    """
    require_role("admin", "agent")
    audit_log("get_forecast", {"city": city, "days": days}, service="mock")
    days = min(max(days, 1), 7)
    base = MOCK_WEATHER.get(city.lower(), {"temp": 20, "condition": "Partly cloudy"})
    conditions = ["Sunny", "Partly cloudy", "Overcast", "Light rain", "Clear skies"]
    lines = []
    for i in range(days):
        date = (datetime.now() + timedelta(days=i + 1)).strftime("%A, %b %d")
        temp_var = random.randint(-4, 4)
        cond = conditions[i % len(conditions)]
        lines.append(f"  {date}: {base['temp'] + temp_var}°C — {cond}")
    return f"Forecast for {city.title()} (next {days} days):\n" + "\n".join(lines)

@mcp.tool()
def get_historical_weather(city: str, date: str) -> str:
    """Get historical weather data for a city on a specific date.

    Args:
        city: Name of the city
        date: Date in YYYY-MM-DD format

    Returns:
        Historical weather conditions for the specified date
    """
    require_role("admin", "agent")
    audit_log("get_historical_weather", {"city": city, "date": date}, service="mock")
    base = MOCK_WEATHER.get(city.lower(), {"temp": 18, "condition": "Clear", "humidity": 65})
    return (
        f"Historical weather for {city.title()} on {date}:\n"
        f"  Temperature: {base['temp'] - 2}°C\n"
        f"  Condition: {base['condition']}\n"
        f"  Humidity: {base['humidity']}%\n"
        f"  Note: Data sourced from local archive"
    )

if __name__ == "__main__":
    auth_msg = f"enabled ({os.environ.get('MCP_AUTH_PROVIDER', 'local')} provider)" if MCP_AUTH_ENABLED else "disabled"
    print(f"Starting Weather MCP Server on port {PORT}...")
    print(f"SSE endpoint : http://localhost:{PORT}/sse")
    print(f"Auth         : {auth_msg}")
    app = mcp.http_app(transport="sse", middleware=mcp_middleware())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
