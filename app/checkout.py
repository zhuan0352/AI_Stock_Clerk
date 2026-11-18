"""Checkout with Stripe, and the order log that feeds the recommender.

Flow (Stripe Checkout, hosted payment page):
1. The browser posts its cart to /api/checkout. We price it from OUR catalog
   (never trusting prices from the browser) and create a Stripe Checkout Session.
2. The shopper pays on Stripe's page and is sent back with the session id.
3. /api/checkout/confirm asks Stripe whether that session is really paid. If so
   the order is written to data/orders.csv and the recommender learns from it.
   A Stripe webhook can do the same thing if the shopper never comes back.

If STRIPE_SECRET_KEY is not set, checkout is simulated (no payment page) so the
rest of the flow still works for demos.
"""
import csv
import secrets
import threading
from datetime import datetime, timezone

import stripe

from . import config

FREE_SHIPPING_OVER = 35.00  # must match data/policies.md
STANDARD_SHIPPING = 5.99
ORDER_COLUMNS = ["order_id", "user_id", "product_id", "quantity", "unit_price", "purchased_at"]


class CheckoutError(Exception):
    pass


def stripe_enabled() -> bool:
    return bool(config.STRIPE_SECRET_KEY)


class OrderLog:
    """Append-only CSV of paid orders. Recording an order twice is a no-op."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        if path.exists():
            with path.open(newline="") as f:
                self._seen = {row["order_id"] for row in csv.DictReader(f)}

    def record(self, order_id: str, user_id: str, lines: list[dict]) -> bool:
        """lines: [{"product": {...}, "quantity": int}]. Returns True if the order is new."""
        with self._lock:
            if order_id in self._seen:
                return False
            is_new_file = not self.path.exists()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.path.open("a", newline="") as f:
                writer = csv.writer(f)
                if is_new_file:
                    writer.writerow(ORDER_COLUMNS)
                for line in lines:
                    product = line["product"]
                    writer.writerow(
                        [order_id, user_id, product["id"], line["quantity"], product["price"], now]
                    )
            self._seen.add(order_id)
            return True


class Checkout:
    def __init__(self, products_by_id: dict, orders: OrderLog, recommender):
        self.products = products_by_id
        self.orders = orders
        self.recommender = recommender
        self._demo_sessions: dict[str, tuple[str, list[dict]]] = {}

    # ---- helpers -----------------------------------------------------------

    def _lines(self, cart: dict[str, int]) -> list[dict]:
        lines = [
            {"product": self.products[pid], "quantity": quantity}
            for pid, quantity in cart.items()
            if pid in self.products and quantity > 0
        ]
        if not lines:
            raise CheckoutError("Your cart is empty.")
        return lines

    @staticmethod
    def _totals(lines: list[dict]) -> dict:
        subtotal = round(sum(l["product"]["price"] * l["quantity"] for l in lines), 2)
        shipping = 0.0 if subtotal >= FREE_SHIPPING_OVER else STANDARD_SHIPPING
        return {"subtotal": subtotal, "shipping": shipping, "total": round(subtotal + shipping, 2)}

    def quote(self, cart: dict[str, int]) -> dict:
        return self._totals(self._lines(cart))

    def _complete(self, order_id: str, user_id: str, lines: list[dict], mode: str, totals=None):
        """Record a paid order and teach the recommender about it."""
        is_new = self.orders.record(order_id, user_id, lines)
        if is_new:
            self.recommender.add_purchases(user_id, [l["product"]["id"] for l in lines])
        return {
            "status": "paid",
            "order_id": order_id,
            "mode": mode,
            "new": is_new,
            "items": lines,
            **(totals or self._totals(lines)),
        }

    # ---- start a checkout --------------------------------------------------

    def start(self, cart: dict[str, int], user_id: str, base_url: str) -> dict:
        """Returns {"url": where to send the shopper, "mode": "stripe" | "demo"}."""
        lines = self._lines(cart)
        totals = self._totals(lines)
        base_url = base_url.rstrip("/")

        if not stripe_enabled():
            session_id = f"demo_{secrets.token_hex(8)}"
            self._demo_sessions[session_id] = (user_id, lines)
            return {"url": f"{base_url}/?checkout=success&session_id={session_id}", "mode": "demo"}

        shipping_name = (
            "Free standard shipping (3–5 business days)"
            if totals["shipping"] == 0
            else "Standard shipping (3–5 business days)"
        )
        try:
            session = stripe.checkout.Session.create(
                api_key=config.STRIPE_SECRET_KEY,
                mode="payment",
                client_reference_id=user_id,
                line_items=[
                    {
                        "quantity": line["quantity"],
                        "price_data": {
                            "currency": "usd",
                            "unit_amount": round(line["product"]["price"] * 100),
                            "product_data": {
                                "name": line["product"]["name"],
                                "description": line["product"]["description"],
                                "metadata": {"product_id": line["product"]["id"]},
                            },
                        },
                    }
                    for line in lines
                ],
                shipping_options=[
                    {
                        "shipping_rate_data": {
                            "type": "fixed_amount",
                            "display_name": shipping_name,
                            "fixed_amount": {
                                "amount": round(totals["shipping"] * 100),
                                "currency": "usd",
                            },
                        }
                    }
                ],
                success_url=f"{base_url}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base_url}/?checkout=cancelled",
            )
        except stripe.StripeError as exc:
            print(f"[checkout] Stripe error creating session: {exc}")
            raise CheckoutError("We couldn't start checkout with Stripe. Please try again.") from exc
        return {"url": session.url, "mode": "stripe"}

    # ---- finish a checkout -------------------------------------------------

    def confirm(self, session_id: str) -> dict:
        """Verify payment for a session and record the order (safe to call repeatedly)."""
        if session_id.startswith("demo_"):
            if stripe_enabled() or session_id not in self._demo_sessions:
                raise CheckoutError("That checkout session was not found.")
            user_id, lines = self._demo_sessions[session_id]
            return self._complete(session_id, user_id, lines, "demo")

        if not stripe_enabled():
            raise CheckoutError("Stripe is not configured.")
        try:
            session = stripe.checkout.Session.retrieve(
                session_id,
                expand=["line_items.data.price.product"],
                api_key=config.STRIPE_SECRET_KEY,
            )
        except stripe.StripeError as exc:
            print(f"[checkout] Stripe error retrieving session: {exc}")
            raise CheckoutError("That checkout session was not found.") from exc

        if session.payment_status != "paid":
            return {"status": session.payment_status or "unpaid", "order_id": session.id}

        lines = []
        for item in session.line_items.data:
            # StripeObjects are not dicts, so convert before using .get()
            metadata = item.price.product.metadata
            product_id = metadata.to_dict().get("product_id") if metadata else None
            if product_id in self.products:
                lines.append({"product": self.products[product_id], "quantity": item.quantity})
        subtotal = (session.amount_subtotal or 0) / 100
        total = (session.amount_total or 0) / 100
        totals = {"subtotal": subtotal, "shipping": round(total - subtotal, 2), "total": total}
        user_id = session.client_reference_id or "guest"
        return self._complete(session.id, user_id, lines, "stripe", totals)

    def handle_webhook(self, payload: bytes, signature: str) -> None:
        """Stripe calls this when a payment completes, even if the shopper closed the tab."""
        try:
            event = stripe.Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise CheckoutError("Invalid webhook signature.") from exc
        if event.type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            self.confirm(event.data.object.id)
