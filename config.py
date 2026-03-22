# config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Gemini
    GEMINI_API_KEY:       str = ""
    GEMINI_MODEL:         str = "gemini-2.5-flash"

    # Amadeus (free test: developers.amadeus.com)
    AMADEUS_API_KEY:      str = ""
    AMADEUS_API_SECRET:   str = ""

    # SerpAPI — optional, DuckDuckGo is free fallback
    SERPAPI_KEY:          str = ""

    # ExchangeRate (free: exchangerate-api.com)
    EXCHANGERATE_API_KEY: str = ""

    # RapidAPI key for Skyscanner (optional)
    RAPIDAPI_KEY:         str = ""

    class Config:
        env_file = ".env"
        extra    = "ignore"   # ← ignore unknown keys from .env

settings = Settings()