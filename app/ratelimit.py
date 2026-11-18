"""Small in-memory rate limits, so a public demo can't burn through the API quota.

Each limit counts requests per client IP in a sliding window. A global daily cap
on AI calls protects the (free tier) Gemini quota even if many people visit.
Limits reset when the server restarts, which is fine for a single-instance demo.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimit:
    def __init__(self, name: str, per_minute: int, per_day: int):
        self.name = name
        self.windows = ((60, per_minute), (86_400, per_day))
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.windows[-1][0]:
                hits.popleft()
            for seconds, limit in self.windows:
                if sum(1 for t in hits if now - t <= seconds) >= limit:
                    raise HTTPException(
                        429,
                        "Too many requests. Please wait a minute and try again."
                        if seconds == 60
                        else "This demo's daily limit was reached. Please try again tomorrow.",
                    )
            hits.append(now)
            if len(self._hits) > 10_000:  # forget idle clients
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]

    def __call__(self, request: Request) -> None:
        """FastAPI dependency: limit per client IP, plus the shared daily budget."""
        client = request.client.host if request.client else "unknown"
        self.check(client)


# The shared budget counts every AI-backed request from everyone.
_ai_budget = RateLimit("ai-total", per_minute=60, per_day=1_500)


class AIRateLimit(RateLimit):
    def __call__(self, request: Request) -> None:
        super().__call__(request)
        _ai_budget.check("all")


chat_limit = AIRateLimit("chat", per_minute=8, per_day=150)
search_limit = AIRateLimit("search", per_minute=20, per_day=400)
checkout_limit = RateLimit("checkout", per_minute=6, per_day=60)
