"""Settings, read from the environment or a local .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
STATIC_DIR = ROOT / "static"

load_dotenv(ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001").strip()
EMBED_DIMENSIONS = int(os.getenv("GEMINI_EMBED_DIMENSIONS", "768"))
# Tried in order; if one is overloaded or unavailable the next one is used.
# The lite models answer in about a second, which suits a live support chat.
# How much the chat model reasons before answering: minimal, low, medium, or high.
THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "medium").strip()
CHAT_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_CHAT_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash"
    ).split(",")
    if m.strip()
]

# Stripe (optional). Without a key, checkout is simulated.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
if STRIPE_SECRET_KEY and not STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_")):
    # This is a demo store with sample products: never take real payments.
    print("[config] STRIPE_SECRET_KEY is not a test-mode key (sk_test_...); ignoring it.")
    STRIPE_SECRET_KEY = ""
