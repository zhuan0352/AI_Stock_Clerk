"""Semantic product search.

Every product description is turned into an embedding vector once (and cached
on disk). A shopper's query is embedded the same way, and products are ranked
by cosine similarity, so "small L-shaped metal thing for furniture bolts"
finds the hex key set even though no words in the name match.

If Gemini is unreachable or no API key is set, search falls back to a local
TF-IDF keyword ranking so the store still works.
"""
import hashlib
import json
import math
import re
import time
from collections import Counter

import numpy as np

from . import config, gemini

STOPWORDS = set(
    "a an and are as at be but by for from has have i in is it its me my of on or "
    "so that the thing things to up with what which you your looking need want like".split()
)
MIN_RELATIVE_SCORE = 0.86  # semantic: hide results that score far below the best match
MIN_SEMANTIC_SCORE = 0.62  # semantic: below this even the best match is unrelated
MIN_RELATIVE_KEYWORD_SCORE = 0.35


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


class SearchEngine:
    def __init__(self, products: list[dict]):
        self.products = products
        self.texts = [
            f"{p['name']}. Category: {p['category']}. {p['description']}" for p in products
        ]
        self.embeddings: np.ndarray | None = None
        self._query_cache: dict[str, np.ndarray] = {}
        self._next_retry = 0.0
        self._build_tfidf()
        self._load_embeddings()

    # ---- Gemini embeddings -------------------------------------------------

    def _cache_path(self):
        digest = hashlib.sha256(
            json.dumps([config.EMBED_MODEL, config.EMBED_DIMENSIONS, self.texts]).encode()
        ).hexdigest()[:16]
        return config.CACHE_DIR / f"product-embeddings-{digest}.npy"

    def _load_embeddings(self) -> None:
        """Load cached product embeddings, or create them with one Gemini call."""
        path = self._cache_path()
        if path.exists():
            self.embeddings = np.load(path)
            return
        if not gemini.available() or time.time() < self._next_retry:
            return
        try:
            vectors = gemini.embed(self.texts, "RETRIEVAL_DOCUMENT")
        except gemini.GeminiError as exc:
            print(f"[search] could not embed products, using keyword search: {exc}")
            self._next_retry = time.time() + 60
            return
        self.embeddings = _normalize(np.array(vectors, dtype=np.float32))
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, self.embeddings)

    def _embed_query(self, query: str) -> np.ndarray:
        key = query.lower()
        if key not in self._query_cache:
            vector = np.array(gemini.embed([query], "RETRIEVAL_QUERY")[0], dtype=np.float32)
            if len(self._query_cache) > 500:
                self._query_cache.clear()
            self._query_cache[key] = _normalize(vector)
        return self._query_cache[key]

    # ---- TF-IDF fallback ---------------------------------------------------

    def _build_tfidf(self) -> None:
        docs = [_tokens(text) for text in self.texts]
        vocabulary = sorted({t for doc in docs for t in doc})
        self._vocab = {t: i for i, t in enumerate(vocabulary)}
        doc_freq = Counter(t for doc in docs for t in set(doc))
        self._idf = np.array(
            [math.log((1 + len(docs)) / (1 + doc_freq[t])) + 1 for t in vocabulary],
            dtype=np.float32,
        )
        matrix = np.zeros((len(docs), len(vocabulary)), dtype=np.float32)
        for row, doc in enumerate(docs):
            for token, count in Counter(doc).items():
                matrix[row, self._vocab[token]] = count
        self._tfidf = _normalize(matrix * self._idf)

    def _keyword_scores(self, query: str) -> np.ndarray:
        vector = np.zeros(len(self._vocab), dtype=np.float32)
        for token, count in Counter(_tokens(query)).items():
            if token in self._vocab:
                vector[self._vocab[token]] = count
        return self._tfidf @ _normalize(vector * self._idf)

    # ---- public API --------------------------------------------------------

    def search(self, query: str, limit: int = 8) -> tuple[list[dict], str]:
        """Return (ranked products with a "score", mode) where mode is "semantic" or "keyword"."""
        query = query.strip()
        if not query:
            return [], "semantic"

        scores, mode = None, "keyword"
        if self.embeddings is None:
            self._load_embeddings()
        if self.embeddings is not None:
            try:
                scores, mode = self.embeddings @ self._embed_query(query), "semantic"
            except gemini.GeminiError as exc:
                print(f"[search] query embedding failed, using keyword search: {exc}")
        if scores is None:
            scores = self._keyword_scores(query)

        order = np.argsort(-scores)[:limit]
        best = float(scores[order[0]])
        if best <= 0 or (mode == "semantic" and best < MIN_SEMANTIC_SCORE):
            return [], mode
        cutoff = best * (MIN_RELATIVE_SCORE if mode == "semantic" else MIN_RELATIVE_KEYWORD_SCORE)
        results = [
            {**self.products[i], "score": round(float(scores[i]), 4)}
            for i in order
            if scores[i] > cutoff or i == order[0]
        ]
        return results, mode
