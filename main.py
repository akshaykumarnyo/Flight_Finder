# main.py
import traceback
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from agent import run_agent
from mcp_tools import (
    search_flights, skyscanner_search, google_search,
    duckduckgo_search, get_exchange_rate, get_airport_info, compare_prices,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="✈ Flight Price Comparison API",
    description=(
        "Gemini 2.5 Flash + 7 MCP Tools\n\n"
        "Tools: Amadeus · Skyscanner · DuckDuckGo · Google Search · "
        "Exchange Rate · Airport Info · Price Comparison"
    ),
    version="2.0.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def global_exc_handler(request, exc):
    tb = traceback.format_exc()
    log.error(f"Unhandled:\n{tb}")
    return JSONResponse(status_code=500,
                        content={"detail": str(exc), "trace": tb.splitlines()[-5:]})


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str

class SearchBody(BaseModel):
    origin:      str
    destination: str
    date:        str
    adults:      Optional[int] = 1


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {
        "service":  "Flight Price Comparison v2",
        "model":    "Gemini 2.5 Flash",
        "tools":    list(["search_flights", "skyscanner_search", "compare_prices",
                          "google_search", "duckduckgo_search",
                          "get_exchange_rate", "get_airport_info"]),
        "docs":     "/docs",
    }


@app.get("/health", tags=["Info"])
def health():
    from config import settings
    return {
        "status":                 "ok",
        "gemini_key":             bool(settings.GEMINI_API_KEY),
        "amadeus_key":            bool(settings.AMADEUS_API_KEY),
        "serpapi_key":            bool(settings.SERPAPI_KEY),
        "exchangerate_key":       bool(settings.EXCHANGERATE_API_KEY),
        "rapidapi_key":           bool(settings.RAPIDAPI_KEY),
        "duckduckgo":             "no key needed ✓",
        "skyscanner_scraper":     "no key needed ✓",
    }


@app.post("/chat", tags=["AI Agent"])
async def chat(req: ChatRequest):
    """
    **Main endpoint** — send any natural language flight query.
    Gemini calls all relevant tools automatically.

    Example: `{"query": "Cheapest flights from Delhi to Dubai on 2025-09-15 for 2 adults"}`
    """
    if not req.query.strip():
        raise HTTPException(400, "Query cannot be empty")
    try:
        return await run_agent(req.query)
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"/chat:\n{tb}")
        raise HTTPException(500, {"error": str(e), "hint": tb.splitlines()[-3:]})


@app.get("/compare", tags=["Tools"])
async def compare(origin: str, destination: str, date: str, adults: int = 1):
    """
    Compare prices from **Amadeus + Skyscanner** in parallel.
    Example: `/compare?origin=DEL&destination=DXB&date=2025-09-15`
    """
    return await compare_prices(origin, destination, date, adults)


@app.get("/flights/amadeus", tags=["Tools"])
async def amadeus(origin: str, destination: str, date: str, adults: int = 1):
    """Live prices from Amadeus API only."""
    result = await search_flights(origin, destination, date, adults)
    return {"source": "amadeus", "flights": result, "count": len(result)}


@app.get("/flights/skyscanner", tags=["Tools"])
async def skyscanner(origin: str, destination: str, date: str, adults: int = 1):
    """
    Prices from Skyscanner (RapidAPI if key set, HTML scraper otherwise).
    Example: `/flights/skyscanner?origin=DEL&destination=LHR&date=2025-09-15`
    """
    result = await skyscanner_search(origin, destination, date, adults)
    return {"source": "skyscanner", "flights": result, "count": len(result)}


@app.get("/search/google", tags=["Tools"])
async def gsearch(q: str, num: int = 5):
    """Google search via SerpAPI (needs SERPAPI_KEY in .env)."""
    return {"results": await google_search(q, num)}


@app.get("/search/duckduckgo", tags=["Tools"])
async def ddgsearch(q: str, num: int = 6):
    """
    **Free** web search via DuckDuckGo — no API key needed.
    Example: `/search/duckduckgo?q=cheapest+flights+DEL+to+LHR+tips`
    """
    return {"results": await duckduckgo_search(q, num)}


@app.get("/exchange", tags=["Tools"])
async def exchange(from_currency: str, to_currency: str):
    """
    Live exchange rate.
    Example: `/exchange?from_currency=USD&to_currency=INR`
    """
    return await get_exchange_rate(from_currency, to_currency)


@app.get("/airport/{iata}", tags=["Tools"])
async def airport(iata: str):
    """
    Airport info from IATA code.
    Example: `/airport/DEL`
    """
    return await get_airport_info(iata)