import os
import sys
import pathlib

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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8003

mcp = FastMCP("Reference Data Lookup Service")

COUNTRIES = {
    "japan":         {"capital": "Tokyo",          "population": "125.7M", "currency": "JPY (Japanese Yen)",    "continent": "Asia",          "language": "Japanese",     "timezone": "JST (UTC+9)"},
    "brazil":        {"capital": "Brasilia",        "population": "215M",   "currency": "BRL (Brazilian Real)",  "continent": "South America", "language": "Portuguese",   "timezone": "BRT (UTC-3)"},
    "germany":       {"capital": "Berlin",          "population": "83.2M",  "currency": "EUR (Euro)",            "continent": "Europe",        "language": "German",       "timezone": "CET (UTC+1)"},
    "india":         {"capital": "New Delhi",       "population": "1.4B",   "currency": "INR (Indian Rupee)",    "continent": "Asia",          "language": "Hindi/English","timezone": "IST (UTC+5:30)"},
    "united states": {"capital": "Washington D.C.", "population": "331M",   "currency": "USD (US Dollar)",       "continent": "North America", "language": "English",      "timezone": "Multiple (EST/CST/MST/PST)"},
    "australia":     {"capital": "Canberra",        "population": "25.5M",  "currency": "AUD (Australian Dollar)","continent": "Oceania",       "language": "English",      "timezone": "AEST (UTC+10)"},
    "france":        {"capital": "Paris",           "population": "67.4M",  "currency": "EUR (Euro)",            "continent": "Europe",        "language": "French",       "timezone": "CET (UTC+1)"},
    "china":         {"capital": "Beijing",         "population": "1.41B",  "currency": "CNY (Chinese Yuan)",    "continent": "Asia",          "language": "Mandarin",     "timezone": "CST (UTC+8)"},
    "united kingdom":{"capital": "London",          "population": "67.2M",  "currency": "GBP (British Pound)",   "continent": "Europe",        "language": "English",      "timezone": "GMT/BST (UTC+0/+1)"},
    "canada":        {"capital": "Ottawa",          "population": "38.2M",  "currency": "CAD (Canadian Dollar)", "continent": "North America", "language": "English/French","timezone": "Multiple (EST/CST/MST/PST)"},
    "singapore":     {"capital": "Singapore City",  "population": "5.9M",   "currency": "SGD (Singapore Dollar)","continent": "Asia",          "language": "English/Malay","timezone": "SGT (UTC+8)"},
}

CURRENCIES = {
    "usd": {"name": "US Dollar",          "symbol": "$",   "country": "United States",    "rate_to_usd": 1.0},
    "eur": {"name": "Euro",               "symbol": "EUR", "country": "European Union",   "rate_to_usd": 1.08},
    "gbp": {"name": "British Pound",      "symbol": "GBP", "country": "United Kingdom",   "rate_to_usd": 1.27},
    "jpy": {"name": "Japanese Yen",       "symbol": "JPY", "country": "Japan",            "rate_to_usd": 0.0067},
    "inr": {"name": "Indian Rupee",       "symbol": "INR", "country": "India",            "rate_to_usd": 0.012},
    "aud": {"name": "Australian Dollar",  "symbol": "AUD", "country": "Australia",        "rate_to_usd": 0.65},
    "cad": {"name": "Canadian Dollar",    "symbol": "CAD", "country": "Canada",           "rate_to_usd": 0.74},
    "brl": {"name": "Brazilian Real",     "symbol": "BRL", "country": "Brazil",           "rate_to_usd": 0.20},
    "cny": {"name": "Chinese Yuan",       "symbol": "CNY", "country": "China",            "rate_to_usd": 0.138},
    "sgd": {"name": "Singapore Dollar",   "symbol": "SGD", "country": "Singapore",        "rate_to_usd": 0.74},
    "chf": {"name": "Swiss Franc",        "symbol": "CHF", "country": "Switzerland",      "rate_to_usd": 1.10},
}

TIMEZONES = {
    "london":      "GMT/BST (UTC+0 / UTC+1 in summer)",
    "new york":    "EST/EDT (UTC-5 / UTC-4 in summer)",
    "tokyo":       "JST (UTC+9, no DST)",
    "sydney":      "AEST/AEDT (UTC+10 / UTC+11 in summer)",
    "dubai":       "GST (UTC+4, no DST)",
    "paris":       "CET/CEST (UTC+1 / UTC+2 in summer)",
    "beijing":     "CST (UTC+8, no DST)",
    "mumbai":      "IST (UTC+5:30, no DST)",
    "berlin":      "CET/CEST (UTC+1 / UTC+2 in summer)",
    "los angeles": "PST/PDT (UTC-8 / UTC-7 in summer)",
    "chicago":     "CST/CDT (UTC-6 / UTC-5 in summer)",
    "singapore":   "SGT (UTC+8, no DST)",
    "toronto":     "EST/EDT (UTC-5 / UTC-4 in summer)",
    "seoul":       "KST (UTC+9, no DST)",
}

@mcp.tool()
def lookup_country(country_name: str) -> str:
    """Look up detailed information about a country.

    Args:
        country_name: Name of the country (e.g., Japan, Brazil, Germany)

    Returns:
        Country info: capital, population, currency, continent, language, timezone
    """
    require_role("admin", "agent")
    audit_log("lookup_country", {"country_name": country_name}, service="reference")
    # Try exact match first, then partial
    key = country_name.lower().strip()
    info = COUNTRIES.get(key)
    if not info:
        for name, data in COUNTRIES.items():
            if key in name or name in key:
                info = data
                key = name
                break
    if info:
        return (
            f"Country: {country_name.title()}\n"
            f"  Capital:    {info['capital']}\n"
            f"  Population: {info['population']}\n"
            f"  Currency:   {info['currency']}\n"
            f"  Continent:  {info['continent']}\n"
            f"  Language:   {info['language']}\n"
            f"  Timezone:   {info['timezone']}"
        )
    return f"Country '{country_name}' not found. Available: {', '.join(k.title() for k in COUNTRIES)}"

@mcp.tool()
def lookup_currency(currency_code: str) -> str:
    """Look up currency information and approximate exchange rate to USD.

    Args:
        currency_code: ISO 4217 currency code (USD, EUR, GBP, JPY, INR, AUD, CAD, BRL, CNY, SGD)

    Returns:
        Currency name, symbol, issuing country, and approximate USD exchange rate
    """
    require_role("admin", "agent")
    audit_log("lookup_currency", {"currency_code": currency_code}, service="reference")
    code = currency_code.upper().strip()
    info = CURRENCIES.get(code.lower())
    if info:
        rate = info["rate_to_usd"]
        return (
            f"Currency: {info['name']} ({code})\n"
            f"  Symbol:  {info['symbol']}\n"
            f"  Country: {info['country']}\n"
            f"  1 USD  = {1 / rate:.2f} {code}\n"
            f"  1 {code} = {rate:.4f} USD\n"
            f"  Note: Rates are approximate reference values"
        )
    supported = ", ".join(k.upper() for k in CURRENCIES)
    return f"Currency '{currency_code}' not found. Supported codes: {supported}"

@mcp.tool()
def lookup_timezone(location: str) -> str:
    """Get timezone information for a city or country.

    Args:
        location: City or country name (e.g., Tokyo, London, India)

    Returns:
        Timezone name, UTC offset, and DST information
    """
    require_role("admin", "agent")
    audit_log("lookup_timezone", {"location": location}, service="reference")
    loc_lower = location.lower().strip()

    # Check cities first
    for city, tz in TIMEZONES.items():
        if loc_lower in city or city in loc_lower:
            return f"Timezone for {location.title()}: {tz}"

    # Check countries
    for country, info in COUNTRIES.items():
        if loc_lower in country or country in loc_lower:
            return f"Timezone for {location.title()} ({country.title()}): {info['timezone']}"

    return f"Timezone for '{location}' not found. Try cities like: {', '.join(k.title() for k in TIMEZONES)}"

if __name__ == "__main__":
    auth_msg = f"enabled ({os.environ.get('MCP_AUTH_PROVIDER', 'local')} provider)" if MCP_AUTH_ENABLED else "disabled"
    print(f"Starting Data Lookup MCP Server on port {PORT}...")
    print(f"SSE endpoint : http://localhost:{PORT}/sse")
    print(f"Auth         : {auth_msg}")
    app = mcp.http_app(transport="sse", middleware=mcp_middleware())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
