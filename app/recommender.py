"""Item-based collaborative filtering from purchase history.

1. Build a shopper x product matrix R, where R[u, i] = 1 if shopper u bought product i.
2. Two products are similar when the same shoppers tend to buy both. That is the
   cosine similarity between their columns of R:

       sim(i, j) = |shoppers who bought both| / sqrt(|bought i| * |bought j|)

3. To recommend for a shopper (or a cart), add up the similarity of every
   candidate product to each product they already have, and return the highest
   scoring products they don't have yet.
4. Every completed checkout is added to the history and the similarities are
   recomputed, so the store's real orders shape future recommendations.
"""
import csv
import threading
from collections import defaultdict
from pathlib import Path

import numpy as np

MIN_CO_PURCHASES = 3  # ignore pairs bought together by only a couple of shoppers


class Recommender:
    def __init__(self, product_ids: list[str], *purchase_files: Path):
        """purchase_files: CSVs with user_id and product_id columns (seed history, store orders)."""
        self.product_ids = product_ids
        self.index = {pid: i for i, pid in enumerate(product_ids)}
        self.history: dict[str, list[str]] = defaultdict(list)
        self._lock = threading.Lock()

        for path in purchase_files:
            if not path.exists():
                continue
            with path.open(newline="") as f:
                for row in csv.DictReader(f):
                    self._remember(row["user_id"], row["product_id"])
        self._fit()

    def _remember(self, user_id: str, product_id: str) -> None:
        if product_id in self.index and product_id not in self.history[user_id]:
            self.history[user_id].append(product_id)

    def _fit(self) -> None:
        """(Re)compute item-item similarity from the full purchase history."""
        users = sorted(self.history)
        interactions = np.zeros((len(users), len(self.product_ids)), dtype=np.float32)
        for u, user in enumerate(users):
            for pid in self.history[user]:
                interactions[u, self.index[pid]] = 1.0

        co_purchases = interactions.T @ interactions  # [i, j] = shoppers who bought both
        popularity = np.diag(co_purchases).copy()  # shoppers who bought each product
        norms = np.sqrt(popularity)
        denominator = np.outer(norms, norms)
        similarity = np.divide(
            co_purchases, denominator, out=np.zeros_like(co_purchases), where=denominator > 0
        )
        similarity[co_purchases < MIN_CO_PURCHASES] = 0.0
        np.fill_diagonal(similarity, 0.0)
        self.popularity, self.similarity = popularity, similarity

    def add_purchases(self, user_id: str, product_ids: list[str]) -> None:
        """Learn from a new order: update the shopper's history and refit the model."""
        with self._lock:
            for pid in product_ids:
                self._remember(user_id, pid)
            self._fit()

    def similar_items(self, product_id: str, limit: int = 4) -> list[dict]:
        """Products most often bought by the same shoppers as product_id."""
        if product_id not in self.index:
            return []
        row = self.similarity[self.index[product_id]]
        return [
            {"id": self.product_ids[j], "score": round(float(row[j]), 4)}
            for j in np.argsort(-row)[:limit]
            if row[j] > 0
        ]

    def recommend(self, owned_ids: list[str], limit: int = 6) -> list[dict]:
        """Recommend products for someone who already has owned_ids (history and/or cart)."""
        owned = [self.index[pid] for pid in dict.fromkeys(owned_ids) if pid in self.index]
        if not owned:
            return self.popular(limit)

        scores = self.similarity[owned].sum(axis=0)
        scores[owned] = -1.0
        results = []
        for j in np.argsort(-scores)[:limit]:
            if scores[j] <= 0:
                break
            # The owned product that contributes most explains the recommendation.
            because = owned[int(np.argmax(self.similarity[owned, j]))]
            results.append(
                {
                    "id": self.product_ids[j],
                    "score": round(float(scores[j]), 4),
                    "because": self.product_ids[because],
                }
            )
        if len(results) < limit:  # not enough signal: top up with best sellers
            seen = set(owned_ids) | {r["id"] for r in results}
            results += [p for p in self.popular(limit * 2) if p["id"] not in seen][
                : limit - len(results)
            ]
        return results

    def popular(self, limit: int = 6) -> list[dict]:
        return [
            {"id": self.product_ids[j], "score": float(self.popularity[j]), "because": None}
            for j in np.argsort(-self.popularity)[:limit]
        ]
