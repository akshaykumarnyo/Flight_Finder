# ✈️ Flight Price Comparison — Simple Edition

**Gemini 2.5 Flash + MCP Tools + FastAPI**  
4 files · zero database · runs in 2 minutes

---

## Stack

| Layer | Tech |
|---|---|
| AI Model | Google Gemini 2.5 Flash |
| Tool Protocol | MCP (3 tools) |
| API | FastAPI |
| Flight Data | Amadeus API (free test) |
| Web Search | SerpAPI (100 free/month) |
| Currency | ExchangeRate API (free) |

---

## Project Structure

```
flight-simple/
├── main.py          ← FastAPI app + all routes
├── agent.py         ← Gemini agent with MCP tool-calling loop
├── mcp_tools.py     ← 3 MCP tools: flights, search, currency
├── config.py        ← settings loaded from .env
├── requirements.txt
└── .env.example     ← copy to .env and fill keys
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Fill in your API keys in .env

# 3. Run
uvicorn main:app --reload
```

Open http://localhost:8000/docs

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/chat` | **Main endpoint** — natural language query, Gemini calls tools |
| POST | `/flights/search` | Direct Amadeus flight search |
| GET  | `/search` | Same as above, browser-friendly |
| GET  | `/web-search?q=...` | Direct Google search |
| GET  | `/exchange?from_currency=USD&to_currency=INR` | Live exchange rate |
| GET  | `/health` | Health check |

---

## Example Usage

### Chat (natural language)
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Cheapest flights from Delhi to London on 2025-08-01 for 2 adults"}'
```

**Response:**
```json
{
  "answer": "Here are the top 3 cheapest flights from DEL to LHR...",
  "tools_called": [
    {"tool": "search_flights", "args": {"origin": "DEL", "destination": "LHR", "date": "2025-08-01", "adults": 2}},
    {"tool": "google_search",  "args": {"query": "cheap flights DEL to LHR August 2025"}},
    {"tool": "get_exchange_rate", "args": {"from_currency": "USD", "to_currency": "INR"}}
  ],
  "flights": [...]
}
```

### Direct Flight Search
```bash
curl "http://localhost:8000/search?origin=DEL&destination=DXB&date=2025-08-10"
```

### Exchange Rate
```bash
curl "http://localhost:8000/exchange?from_currency=USD&to_currency=INR"
```

---

## How It Works

```
User query
    ↓
FastAPI /chat
    ↓
Gemini 2.5 Flash (agent.py)
    ↓  decides which tools to call
MCP Tools (mcp_tools.py)
    ├── search_flights  → Amadeus API
    ├── google_search   → SerpAPI
    └── get_exchange_rate → ExchangeRate API
    ↓  tool results fed back to Gemini
Gemini generates final answer
    ↓
JSON response to user
```

---

## Get Free API Keys

| Service | URL | Free Tier |
|---|---|---|
| **Gemini** | https://aistudio.google.com/app/apikey | Generous free tier |
| **Amadeus** | https://developers.amadeus.com | Test environment free |
| **SerpAPI** | https://serpapi.com | 100 searches/month |
| **ExchangeRate** | https://www.exchangerate-api.com | 1500 requests/month |
