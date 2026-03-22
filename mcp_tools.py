# mcp_tools.py
# ─────────────────────────────────────────────────────────────────────────────
# 7 MCP Tools:
#   1. search_flights        — Amadeus live prices
#   2. skyscanner_search     — Skyscanner scraper (HTML + RapidAPI fallback)
#   3. google_search         — SerpAPI Google search
#   4. duckduckgo_search     — DuckDuckGo (NO API key needed)
#   5. get_exchange_rate     — ExchangeRate API
#   6. get_airport_info      — IATA code lookup
#   7. compare_prices        — merge + rank Amadeus vs Skyscanner results
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from config import settings

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMADEUS — live flight search
# ══════════════════════════════════════════════════════════════════════════════

async def search_flights(origin: str, destination: str, date: str, adults: int = 1) -> list:
    """MCP Tool — Live flight prices from Amadeus API."""
    log.info(f"[amadeus] {origin}→{destination} {date} x{adults}")

    if not settings.AMADEUS_API_KEY:
        log.warning("[amadeus] No API key — returning mock data")
        return _mock_flights(origin, destination, source="amadeus")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            tr = await client.post(
                "https://test.api.amadeus.com/v1/security/oauth2/token",
                data={"grant_type": "client_credentials",
                      "client_id": settings.AMADEUS_API_KEY,
                      "client_secret": settings.AMADEUS_API_SECRET},
            )
            tr.raise_for_status()
            token = tr.json().get("access_token", "")

            resp = await client.get(
                "https://test.api.amadeus.com/v2/shopping/flight-offers",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "originLocationCode":      origin.upper(),
                    "destinationLocationCode": destination.upper(),
                    "departureDate":           date,
                    "adults":                  adults,
                    "max":                     10,
                    "currencyCode":            "USD",
                },
            )
            resp.raise_for_status()
            offers = resp.json().get("data", [])

        results = []
        for o in offers:
            try:
                seg   = o["itineraries"][0]["segments"][0]
                price = float(o["price"]["grandTotal"])
                results.append({
                    "source":         "amadeus",
                    "airline":        seg.get("carrierCode", ""),
                    "flight_number":  seg.get("carrierCode", "") + seg.get("number", ""),
                    "departure_time": seg["departure"]["at"],
                    "arrival_time":   seg["arrival"]["at"],
                    "duration":       o["itineraries"][0].get("duration", ""),
                    "stops":          len(o["itineraries"][0]["segments"]) - 1,
                    "price_usd":      price,
                    "cabin":          (o.get("travelerPricings") or [{}])[0]
                                       .get("fareDetailsBySegment", [{}])[0]
                                       .get("cabin", "ECONOMY"),
                })
            except (KeyError, IndexError):
                continue

        log.info(f"[amadeus] {len(results)} flights found")
        return sorted(results, key=lambda x: x["price_usd"])

    except Exception as e:
        log.error(f"[amadeus] error: {e}")
        return [{"error": str(e), "source": "amadeus"}]


# ══════════════════════════════════════════════════════════════════════════════
# 2. SKYSCANNER — scraper + optional RapidAPI
# ══════════════════════════════════════════════════════════════════════════════

async def skyscanner_search(origin: str, destination: str, date: str, adults: int = 1) -> list:
    """
    MCP Tool — Fetch flight prices from Skyscanner.
    Tries RapidAPI first (if RAPIDAPI_KEY set), falls back to HTML scraping.
    """
    log.info(f"[skyscanner] {origin}→{destination} {date}")

    # ── Option A: RapidAPI Skyscanner endpoint ────────────────────────────────
    if settings.RAPIDAPI_KEY:
        try:
            result = await _skyscanner_rapidapi(origin, destination, date, adults)
            if result and "error" not in result[0]:
                return result
        except Exception as e:
            log.warning(f"[skyscanner] RapidAPI failed: {e}, trying scraper")

    # ── Option B: HTML scraper ────────────────────────────────────────────────
    try:
        return await _skyscanner_scrape(origin, destination, date)
    except Exception as e:
        log.error(f"[skyscanner] scraper error: {e}")
        return [{"error": str(e), "source": "skyscanner"}]


async def _skyscanner_rapidapi(origin: str, destination: str, date: str, adults: int) -> list:
    """Call Sky-Scanner3 on RapidAPI."""
    # Convert YYYY-MM-DD → YYYYMMDD for Skyscanner
    date_fmt = date.replace("-", "")

    async with httpx.AsyncClient(timeout=20) as client:
        # Step 1: create search session
        resp = await client.post(
            "https://sky-scanner3.p.rapidapi.com/flights/search-one-way",
            headers={
                "X-RapidAPI-Key":  settings.RAPIDAPI_KEY,
                "X-RapidAPI-Host": "sky-scanner3.p.rapidapi.com",
                "Content-Type":    "application/json",
            },
            json={
                "fromEntityId": origin.upper(),
                "toEntityId":   destination.upper(),
                "departDate":   date,
                "adults":       adults,
                "currency":     "USD",
                "locale":       "en-US",
                "market":       "US",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    itineraries = (data.get("data") or {}).get("itineraries") or []
    results = []
    for item in itineraries[:10]:
        try:
            leg    = item["legs"][0]
            price  = float(item["price"]["raw"])
            results.append({
                "source":         "skyscanner",
                "airline":        leg["carriers"]["marketing"][0]["name"],
                "flight_number":  leg["carriers"]["marketing"][0]["alternateId"] + str(leg.get("flightNumbers", ["?"])[0]),
                "departure_time": leg["departure"],
                "arrival_time":   leg["arrival"],
                "duration":       f"PT{leg['durationInMinutes'] // 60}H{leg['durationInMinutes'] % 60}M",
                "stops":          leg["stopCount"],
                "price_usd":      price,
                "cabin":          "ECONOMY",
                "deep_link":      item.get("deeplink", ""),
            })
        except (KeyError, IndexError, TypeError):
            continue

    log.info(f"[skyscanner-rapidapi] {len(results)} results")
    return sorted(results, key=lambda x: x["price_usd"])


async def _skyscanner_scrape(origin: str, destination: str, date: str) -> list:
    """
    Scrape Skyscanner explore page for price hints.
    Note: Skyscanner's main search requires JS; this hits the lighter explore endpoint.
    """
    # Use the Skyscanner browse quotes API (no auth needed, returns JSON)
    date_fmt = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m")  # YYYY-MM for browse

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    url = (
        f"https://www.skyscanner.net/g/conductor/v1/fps3/search/"
        f"?market=IN&locale=en-GB&currency=USD"
        f"&querystring={origin.upper()}-sky%3B{destination.upper()}-sky%3B{date}"
        f"&adults=1&children=0&infants=0&cabinclass=economy"
    )

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)

    results = []

    if resp.status_code == 200:
        try:
            data = resp.json()
            itineraries = (data.get("itineraries") or {}).get("results") or []
            for item in itineraries[:8]:
                try:
                    price = float(item["pricingOptions"][0]["price"]["amount"])
                    leg   = item["legs"][0]
                    results.append({
                        "source":         "skyscanner",
                        "airline":        leg.get("carriers", [{}])[0].get("name", "Unknown"),
                        "departure_time": leg.get("departureDateTime", {}).get("isoStr", date + "T00:00:00"),
                        "arrival_time":   leg.get("arrivalDateTime", {}).get("isoStr", ""),
                        "duration":       f"PT{leg.get('durationInMinutes', 0) // 60}H{leg.get('durationInMinutes', 0) % 60}M",
                        "stops":          len(leg.get("stopIds", [])),
                        "price_usd":      price,
                        "cabin":          "ECONOMY",
                        "deep_link":      item.get("deeplink", "https://www.skyscanner.net"),
                    })
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
        except Exception:
            pass

    if not results:
        # Minimal fallback: return a Skyscanner deep-link so user can check manually
        log.warning("[skyscanner-scrape] Could not parse results — returning deep link")
        results = [{
            "source":    "skyscanner",
            "note":      "Live prices unavailable — visit Skyscanner directly",
            "deep_link": f"https://www.skyscanner.net/transport/flights/{origin.lower()}/{destination.lower()}/{date.replace('-', '')}/",
            "price_usd": None,
        }]

    log.info(f"[skyscanner-scrape] {len(results)} results")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 3. GOOGLE SEARCH — SerpAPI
# ══════════════════════════════════════════════════════════════════════════════

async def google_search(query: str, num: int = 5) -> list:
    """MCP Tool — Google search via SerpAPI (needs SERPAPI_KEY)."""
    log.info(f"[google] {query!r}")

    if not settings.SERPAPI_KEY:
        log.warning("[google] No SERPAPI_KEY — skipping (use DuckDuckGo instead)")
        return [{"note": "SERPAPI_KEY not set. Use duckduckgo_search instead.", "query": query}]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://serpapi.com/search",
                params={"q": query, "num": num, "api_key": settings.SERPAPI_KEY, "engine": "google"},
            )
            resp.raise_for_status()
        items = resp.json().get("organic_results", [])
        return [{"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet")} for r in items]
    except Exception as e:
        log.error(f"[google] error: {e}")
        return [{"error": str(e)}]


# ══════════════════════════════════════════════════════════════════════════════
# 4. DUCKDUCKGO SEARCH — no API key needed
# ══════════════════════════════════════════════════════════════════════════════

async def duckduckgo_search(query: str, num: int = 6) -> list:
    """
    MCP Tool — Web search via DuckDuckGo. No API key required.
    Uses duckduckgo-search library (DDGS).
    """
    log.info(f"[ddg] {query!r}")
    try:
        from duckduckgo_search import DDGS
        # DDGS is synchronous — run in executor to avoid blocking
        loop = asyncio.get_event_loop()

        def _search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=num))

        results = await loop.run_in_executor(None, _search)

        return [
            {
                "title":   r.get("title"),
                "link":    r.get("href"),
                "snippet": r.get("body"),
                "source":  "duckduckgo",
            }
            for r in results
        ]
    except ImportError:
        return [{"error": "duckduckgo-search not installed. Run: pip install duckduckgo-search"}]
    except Exception as e:
        log.error(f"[ddg] error: {e}")
        # Fallback: use DuckDuckGo HTML endpoint directly
        return await _ddg_html_fallback(query, num)


async def _ddg_html_fallback(query: str, num: int) -> list:
    """Direct DuckDuckGo HTML scrape as fallback."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
            )
        soup    = BeautifulSoup(resp.text, "lxml")
        results = []
        for result in soup.select(".result")[:num]:
            title_el   = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            link_el    = result.select_one(".result__url")
            if title_el:
                results.append({
                    "title":   title_el.get_text(strip=True),
                    "link":    link_el.get_text(strip=True) if link_el else "",
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    "source":  "duckduckgo",
                })
        return results
    except Exception as e:
        return [{"error": f"DDG fallback failed: {e}"}]


# ══════════════════════════════════════════════════════════════════════════════
# 5. EXCHANGE RATE
# ══════════════════════════════════════════════════════════════════════════════

async def get_exchange_rate(from_currency: str, to_currency: str) -> dict:
    """MCP Tool — Live exchange rate."""
    log.info(f"[exchange] {from_currency}→{to_currency}")

    if not settings.EXCHANGERATE_API_KEY:
        approx = {"USD": 83.5, "EUR": 90.2, "GBP": 106.4, "AED": 22.7, "SGD": 62.0}
        rate   = approx.get(from_currency.upper(), 1.0) if to_currency.upper() == "INR" else 1.0
        return {"from": from_currency.upper(), "to": to_currency.upper(), "rate": rate, "source": "fallback"}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"https://v6.exchangerate-api.com/v6/{settings.EXCHANGERATE_API_KEY}"
                f"/pair/{from_currency.upper()}/{to_currency.upper()}"
            )
            resp.raise_for_status()
        data = resp.json()
        return {"from": from_currency.upper(), "to": to_currency.upper(),
                "rate": data.get("conversion_rate", 1.0), "source": "exchangerate-api"}
    except Exception as e:
        log.error(f"[exchange] error: {e}")
        return {"error": str(e), "from": from_currency, "to": to_currency, "rate": 83.5}


# ══════════════════════════════════════════════════════════════════════════════
# 6. AIRPORT INFO — IATA code lookup (no API key needed)
# ══════════════════════════════════════════════════════════════════════════════

# Common airport lookup table (extend as needed)
_AIRPORTS = {
    "DEL": {"name": "Indira Gandhi International", "city": "New Delhi",    "country": "India",          "timezone": "Asia/Kolkata"},
    "BOM": {"name": "Chhatrapati Shivaji Maharaj", "city": "Mumbai",       "country": "India",          "timezone": "Asia/Kolkata"},
    "BLR": {"name": "Kempegowda International",    "city": "Bengaluru",    "country": "India",          "timezone": "Asia/Kolkata"},
    "HYD": {"name": "Rajiv Gandhi International",  "city": "Hyderabad",    "country": "India",          "timezone": "Asia/Kolkata"},
    "MAA": {"name": "Chennai International",        "city": "Chennai",      "country": "India",          "timezone": "Asia/Kolkata"},
    "CCU": {"name": "Netaji Subhas Chandra Bose",  "city": "Kolkata",      "country": "India",          "timezone": "Asia/Kolkata"},
    "LHR": {"name": "Heathrow",                     "city": "London",       "country": "UK",             "timezone": "Europe/London"},
    "LGW": {"name": "Gatwick",                      "city": "London",       "country": "UK",             "timezone": "Europe/London"},
    "CDG": {"name": "Charles de Gaulle",            "city": "Paris",        "country": "France",         "timezone": "Europe/Paris"},
    "DXB": {"name": "Dubai International",          "city": "Dubai",        "country": "UAE",            "timezone": "Asia/Dubai"},
    "AUH": {"name": "Abu Dhabi International",      "city": "Abu Dhabi",    "country": "UAE",            "timezone": "Asia/Dubai"},
    "SIN": {"name": "Changi",                       "city": "Singapore",    "country": "Singapore",      "timezone": "Asia/Singapore"},
    "KUL": {"name": "Kuala Lumpur International",   "city": "Kuala Lumpur", "country": "Malaysia",       "timezone": "Asia/Kuala_Lumpur"},
    "BKK": {"name": "Suvarnabhumi",                 "city": "Bangkok",      "country": "Thailand",       "timezone": "Asia/Bangkok"},
    "JFK": {"name": "John F. Kennedy",              "city": "New York",     "country": "USA",            "timezone": "America/New_York"},
    "LAX": {"name": "Los Angeles International",    "city": "Los Angeles",  "country": "USA",            "timezone": "America/Los_Angeles"},
    "ORD": {"name": "O'Hare International",         "city": "Chicago",      "country": "USA",            "timezone": "America/Chicago"},
    "SFO": {"name": "San Francisco International",  "city": "San Francisco","country": "USA",            "timezone": "America/Los_Angeles"},
    "FRA": {"name": "Frankfurt Airport",            "city": "Frankfurt",    "country": "Germany",        "timezone": "Europe/Berlin"},
    "AMS": {"name": "Amsterdam Schiphol",           "city": "Amsterdam",    "country": "Netherlands",    "timezone": "Europe/Amsterdam"},
    "NRT": {"name": "Narita International",         "city": "Tokyo",        "country": "Japan",          "timezone": "Asia/Tokyo"},
    "HND": {"name": "Haneda",                       "city": "Tokyo",        "country": "Japan",          "timezone": "Asia/Tokyo"},
    "SYD": {"name": "Sydney Kingsford Smith",       "city": "Sydney",       "country": "Australia",      "timezone": "Australia/Sydney"},
    "DOH": {"name": "Hamad International",          "city": "Doha",         "country": "Qatar",          "timezone": "Asia/Qatar"},
    "ICN": {"name": "Incheon International",        "city": "Seoul",        "country": "South Korea",    "timezone": "Asia/Seoul"},
}

async def get_airport_info(iata_code: str) -> dict:
    """
    MCP Tool — Get airport details (name, city, country, timezone) from IATA code.
    Also tries to look up unknown codes via DuckDuckGo.
    """
    code = iata_code.upper().strip()
    log.info(f"[airport] lookup: {code}")

    if code in _AIRPORTS:
        return {"iata": code, **_AIRPORTS[code]}

    # Not in local table — search online
    try:
        results = await duckduckgo_search(f"{code} airport IATA code city country", num=3)
        snippet = results[0].get("snippet", "") if results else ""
        return {
            "iata":   code,
            "note":   "Not in local table — web result below",
            "snippet": snippet,
        }
    except Exception:
        return {"iata": code, "error": "Unknown IATA code"}


# ══════════════════════════════════════════════════════════════════════════════
# 7. COMPARE PRICES — merge Amadeus + Skyscanner and rank by value
# ══════════════════════════════════════════════════════════════════════════════

async def compare_prices(origin: str, destination: str, date: str, adults: int = 1) -> dict:
    """
    MCP Tool — Fetch flights from BOTH Amadeus and Skyscanner in parallel,
    merge results, and return a ranked comparison with cheapest-per-source.
    """
    log.info(f"[compare] {origin}→{destination} {date}")

    # Run both searches in parallel
    amadeus_task    = asyncio.create_task(search_flights(origin, destination, date, adults))
    skyscanner_task = asyncio.create_task(skyscanner_search(origin, destination, date, adults))

    amadeus_results, skyscanner_results = await asyncio.gather(amadeus_task, skyscanner_task)

    # Filter errors
    amadeus_ok    = [f for f in amadeus_results    if "error" not in f and f.get("price_usd")]
    skyscanner_ok = [f for f in skyscanner_results if "error" not in f and f.get("price_usd")]

    all_flights = amadeus_ok + skyscanner_ok
    all_flights.sort(key=lambda x: x.get("price_usd", 9999))

    cheapest_amadeus    = amadeus_ok[0]    if amadeus_ok    else None
    cheapest_skyscanner = skyscanner_ok[0] if skyscanner_ok else None

    # Price difference
    price_diff = None
    if cheapest_amadeus and cheapest_skyscanner:
        price_diff = round(abs(cheapest_amadeus["price_usd"] - cheapest_skyscanner["price_usd"]), 2)

    return {
        "origin":              origin.upper(),
        "destination":         destination.upper(),
        "date":                date,
        "total_results":       len(all_flights),
        "cheapest_overall":    all_flights[0] if all_flights else None,
        "cheapest_amadeus":    cheapest_amadeus,
        "cheapest_skyscanner": cheapest_skyscanner,
        "price_difference_usd": price_diff,
        "all_flights":         all_flights[:10],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Mock data (used when API keys are missing)
# ══════════════════════════════════════════════════════════════════════════════

def _mock_flights(origin: str, destination: str, source: str = "mock") -> list:
    return [
        {"source": source, "airline": "AI", "flight_number": "AI101",
         "departure_time": "2025-08-01T06:00:00", "arrival_time": "2025-08-01T18:30:00",
         "duration": "PT12H30M", "stops": 0, "price_usd": 540.0, "cabin": "ECONOMY"},
        {"source": source, "airline": "EK", "flight_number": "EK512",
         "departure_time": "2025-08-01T02:30:00", "arrival_time": "2025-08-01T14:00:00",
         "duration": "PT11H30M", "stops": 1, "price_usd": 480.0, "cabin": "ECONOMY"},
        {"source": source, "airline": "QR", "flight_number": "QR571",
         "departure_time": "2025-08-01T08:00:00", "arrival_time": "2025-08-01T19:00:00",
         "duration": "PT11H00M", "stops": 1, "price_usd": 510.0, "cabin": "ECONOMY"},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Tool registry + Gemini declarations
# ══════════════════════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    "search_flights":    search_flights,
    "skyscanner_search": skyscanner_search,
    "google_search":     google_search,
    "duckduckgo_search": duckduckgo_search,
    "get_exchange_rate": get_exchange_rate,
    "get_airport_info":  get_airport_info,
    "compare_prices":    compare_prices,
}

import google.generativeai.types as genai_types

GEMINI_TOOLS = [
    genai_types.Tool(
        function_declarations=[

            genai_types.FunctionDeclaration(
                name="search_flights",
                description="Search live flight prices via Amadeus API.",
                parameters={
                    "type": "object",
                    "properties": {
                        "origin":      {"type": "string", "description": "IATA code e.g. DEL"},
                        "destination": {"type": "string", "description": "IATA code e.g. LHR"},
                        "date":        {"type": "string", "description": "YYYY-MM-DD"},
                        "adults":      {"type": "integer", "description": "Passengers (default 1)"},
                    },
                    "required": ["origin", "destination", "date"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="skyscanner_search",
                description="Fetch flight prices from Skyscanner (scraper + RapidAPI).",
                parameters={
                    "type": "object",
                    "properties": {
                        "origin":      {"type": "string", "description": "IATA code e.g. DEL"},
                        "destination": {"type": "string", "description": "IATA code e.g. LHR"},
                        "date":        {"type": "string", "description": "YYYY-MM-DD"},
                        "adults":      {"type": "integer", "description": "Passengers (default 1)"},
                    },
                    "required": ["origin", "destination", "date"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="compare_prices",
                description="Compare flight prices from BOTH Amadeus AND Skyscanner simultaneously. Use this for the best price comparison.",
                parameters={
                    "type": "object",
                    "properties": {
                        "origin":      {"type": "string", "description": "IATA code e.g. DEL"},
                        "destination": {"type": "string", "description": "IATA code e.g. LHR"},
                        "date":        {"type": "string", "description": "YYYY-MM-DD"},
                        "adults":      {"type": "integer", "description": "Passengers (default 1)"},
                    },
                    "required": ["origin", "destination", "date"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="google_search",
                description="Search Google for flight deals, visa info, airline policies (requires SERPAPI_KEY).",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num":   {"type": "integer", "description": "Results count (default 5)"},
                    },
                    "required": ["query"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="duckduckgo_search",
                description="Search the web using DuckDuckGo — no API key needed. Use for travel tips, airline reviews, baggage policies.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num":   {"type": "integer", "description": "Results count (default 6)"},
                    },
                    "required": ["query"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="get_exchange_rate",
                description="Get live currency exchange rate between two currencies.",
                parameters={
                    "type": "object",
                    "properties": {
                        "from_currency": {"type": "string", "description": "Source currency e.g. USD"},
                        "to_currency":   {"type": "string", "description": "Target currency e.g. INR"},
                    },
                    "required": ["from_currency", "to_currency"],
                },
            ),

            genai_types.FunctionDeclaration(
                name="get_airport_info",
                description="Get airport details (name, city, country, timezone) from an IATA code.",
                parameters={
                    "type": "object",
                    "properties": {
                        "iata_code": {"type": "string", "description": "3-letter IATA code e.g. DEL"},
                    },
                    "required": ["iata_code"],
                },
            ),

        ]
    )
]