# agent.py
import json
import logging
import google.generativeai as genai
from config import settings
from mcp_tools import TOOL_REGISTRY, GEMINI_TOOLS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)

SYSTEM_PROMPT = """You are an expert flight price comparison assistant with access to 7 tools.

For every flight query you MUST:
1. Call compare_prices — fetches from BOTH Amadeus AND Skyscanner simultaneously.
2. Call duckduckgo_search — find deals, tips, and baggage policies for the route.
3. Call get_exchange_rate (USD→INR) — so prices are shown in INR too.
4. Optionally call get_airport_info for either airport if useful.

Return a structured answer with:
## Cheapest Flights Found
- List top 3 cheapest (source, airline, price USD + INR, times, stops, duration)
- Note which platform is cheapest (Amadeus vs Skyscanner)

## Travel Tips
- 2-3 practical tips from web search (baggage, visa, best booking time)

## Booking Advice
- Short recommendation on when/where to book

Always use IATA codes: Delhi=DEL, Mumbai=BOM, London=LHR, Dubai=DXB, Singapore=SIN,
New York=JFK, Bangkok=BKK, Kolkata=CCU, Chennai=MAA, Bengaluru=BLR, Hyderabad=HYD.
"""


async def run_agent(user_query: str) -> dict:
    """Gemini 1.5 Flash agent with 7 MCP tools."""
    log.info(f"Agent query: {user_query}")

    try:
        model = genai.GenerativeModel(
            model_name=settings.GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            tools=GEMINI_TOOLS,
            tool_config={"function_calling_config": {"mode": "AUTO"}},
        )
    except Exception as e:
        raise RuntimeError(f"Gemini model init failed: {e}")

    chat         = model.start_chat(enable_automatic_function_calling=False)
    tools_called = []
    flights_data = []

    try:
        response = chat.send_message(user_query)
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")

    # ── Agentic tool-call loop ────────────────────────────────────────────────
    for round_num in range(8):
        fn_calls = [
            part.function_call
            for part in response.parts
            if getattr(part, "function_call", None) and part.function_call.name
        ]

        if not fn_calls:
            log.info(f"Agent done after {round_num} rounds")
            break

        response_parts = []
        for fc in fn_calls:
            fn_name = fc.name
            fn_args = dict(fc.args)
            tools_called.append({"tool": fn_name, "args": fn_args})
            log.info(f"  [{round_num}] {fn_name}({fn_args})")

            try:
                if fn_name in TOOL_REGISTRY:
                    result = await TOOL_REGISTRY[fn_name](**fn_args)
                    # Collect flight data
                    if fn_name in ("search_flights", "skyscanner_search"):
                        flights_data += [r for r in result if "error" not in r and r.get("price_usd")]
                    elif fn_name == "compare_prices" and isinstance(result, dict):
                        flights_data += result.get("all_flights", [])
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}
            except Exception as e:
                log.error(f"  Tool {fn_name} error: {e}")
                result = {"error": str(e)}

            response_parts.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fn_name,
                        response={"result": json.dumps(result, default=str)},
                    )
                )
            )

        try:
            response = chat.send_message(response_parts)
        except Exception as e:
            raise RuntimeError(f"Gemini tool-result error: {e}")

    # Deduplicate flights by flight_number + price
    seen         = set()
    unique_flights = []
    for f in flights_data:
        key = (f.get("flight_number", ""), f.get("price_usd", 0))
        if key not in seen:
            seen.add(key)
            unique_flights.append(f)
    unique_flights.sort(key=lambda x: x.get("price_usd", 9999))

    answer = "".join(
        getattr(part, "text", "") for part in response.parts
    ).strip() or "Search complete — see flights list."

    return {
        "answer":       answer,
        "tools_called": tools_called,
        "flights":      unique_flights[:8],
    }