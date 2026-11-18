"""Stock Clerk: web API + static storefront.

Run with:  uvicorn app.main:app --reload
"""
import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, gemini
from .chatbot import SupportBot
from .checkout import FREE_SHIPPING_OVER, STANDARD_SHIPPING, Checkout, CheckoutError, OrderLog, stripe_enabled
from .ratelimit import chat_limit, checkout_limit, search_limit
from .recommender import Recommender
from .search import SearchEngine

products: list[dict] = json.loads((config.DATA_DIR / "products.json").read_text())
by_id = {p["id"]: p for p in products}

search_engine = SearchEngine(products)
ORDERS_CSV = config.DATA_DIR / "orders.csv"  # orders placed through this store's checkout
recommender = Recommender(
    [p["id"] for p in products], config.DATA_DIR / "purchases.csv", ORDERS_CSV
)
checkout = Checkout(by_id, OrderLog(ORDERS_CSV), recommender)
support_bot = SupportBot(search_engine, products)

# A few shoppers from the purchase history, so the UI can show personalised picks.
demo_shoppers = [
    user
    for user, items in sorted(recommender.history.items())
    if user.startswith("u") and 5 <= len(items) <= 9
][:6]

app = FastAPI(title="Stock Clerk")


def _with_product(entries: list[dict]) -> list[dict]:
    """Attach full product data (and the "because" product name) to recommender output."""
    out = []
    for entry in entries:
        because = by_id.get(entry.get("because") or "")
        out.append(
            {
                **by_id[entry["id"]],
                "score": entry["score"],
                "because": {"id": because["id"], "name": because["name"]} if because else None,
            }
        )
    return out


@app.get("/api/status")
def status():
    return {
        "ai_enabled": gemini.available(),
        "semantic_search_ready": search_engine.embeddings is not None,
        "embed_model": config.EMBED_MODEL,
        "chat_models": config.CHAT_MODELS,
        "products": len(products),
        "shoppers_in_history": len(recommender.history),
        "checkout": {
            "stripe": stripe_enabled(),
            "free_shipping_over": FREE_SHIPPING_OVER,
            "standard_shipping": STANDARD_SHIPPING,
        },
    }


@app.get("/api/products")
def list_products():
    return {"products": products, "categories": sorted({p["category"] for p in products})}


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    if product_id not in by_id:
        raise HTTPException(404, "Product not found")
    return {
        "product": by_id[product_id],
        "also_bought": _with_product(recommender.similar_items(product_id, limit=4)),
    }


@app.get("/api/search", dependencies=[Depends(search_limit)])
def search(q: str = Query(..., min_length=1, max_length=300)):
    results, mode = search_engine.search(q, limit=8)
    return {"query": q, "mode": mode, "results": results}


@app.get("/api/shoppers")
def shoppers():
    return {
        "shoppers": [
            {
                "id": user,
                "label": f"Shopper {user[1:].lstrip('0')}",
                "purchases": [by_id[pid] for pid in recommender.history[user]],
            }
            for user in demo_shoppers
        ]
    }


class RecommendRequest(BaseModel):
    shopper_id: str | None = Field(default=None, max_length=40)
    cart: list[str] = Field(default_factory=list, max_length=100)


@app.post("/api/recommendations")
def recommendations(request: RecommendRequest):
    history = recommender.history.get(request.shopper_id or "", [])
    owned = list(history) + [pid for pid in request.cart if pid in by_id]
    return {
        "personalized": bool(owned),
        "history": [by_id[pid] for pid in history],
        "recommendations": _with_product(recommender.recommend(owned, limit=6)),
    }


class CheckoutRequest(BaseModel):
    cart: dict[str, int] = Field(max_length=100)  # product id -> quantity
    shopper_id: str = Field(default="guest", pattern="^[a-z0-9-]{1,40}$")


@app.post("/api/checkout", dependencies=[Depends(checkout_limit)])
def start_checkout(body: CheckoutRequest, request: Request):
    cart = {pid: min(quantity, 99) for pid, quantity in body.cart.items()}
    try:
        return checkout.start(cart, body.shopper_id, str(request.base_url))
    except CheckoutError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/checkout/confirm")
def confirm_checkout(session_id: str = Query(..., pattern="^[A-Za-z0-9_]{1,200}$")):
    try:
        return checkout.confirm(session_id)
    except CheckoutError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not config.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(404, "Webhook is not configured")
    try:
        checkout.handle_webhook(await request.body(), request.headers.get("stripe-signature", ""))
    except CheckoutError as exc:
        raise HTTPException(400, str(exc))
    return {"received": True}


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


@app.post("/api/chat", dependencies=[Depends(chat_limit)])
def chat(request: ChatRequest):
    return support_bot.reply([m.model_dump() for m in request.messages])


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
