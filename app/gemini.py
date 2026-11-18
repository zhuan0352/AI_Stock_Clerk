"""Thin client for the two Gemini REST endpoints the app uses.

- embed():    text -> vectors, for semantic search
- generate(): chat completion, for the support chatbot
"""
import time

import httpx

from . import config

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE = {404, 429, 500, 502, 503, 504}  # fall through to the next chat model
COOLDOWN_SECONDS = 300

CHAT_TIMEOUT_SECONDS = 12.0  # per model, before moving on to the next one

_http = httpx.Client()
_cooldown_until: dict[str, float] = {}


class GeminiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _post(path: str, body: dict, timeout: float = 20.0) -> dict:
    if not available():
        raise GeminiError("GEMINI_API_KEY is not set")
    try:
        response = _http.post(
            f"{BASE_URL}/{path}",
            json=body,
            headers={"x-goog-api-key": config.GEMINI_API_KEY},
            timeout=httpx.Timeout(timeout, connect=5.0),
        )
    except httpx.HTTPError as exc:
        raise GeminiError(f"network error: {type(exc).__name__}") from exc
    if response.status_code != 200:
        try:
            message = response.json()["error"]["message"]
        except Exception:
            message = response.text[:200]
        raise GeminiError(f"HTTP {response.status_code}: {message}", response.status_code)
    return response.json()


def embed(texts: list[str], task_type: str) -> list[list[float]]:
    """Embed texts. task_type is RETRIEVAL_DOCUMENT for products, RETRIEVAL_QUERY for searches."""
    model = f"models/{config.EMBED_MODEL}"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 50):
        body = {
            "requests": [
                {
                    "model": model,
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": config.EMBED_DIMENSIONS,
                }
                for text in texts[start : start + 50]
            ]
        }
        data = _post(f"{model}:batchEmbedContents", body)
        vectors.extend(item["values"] for item in data["embeddings"])
    return vectors


def generate(
    system: str,
    contents: list[dict],
    response_schema: dict | None = None,
    temperature: float = 0.3,
) -> str:
    """Return the model's text reply, trying each configured chat model in turn."""
    generation_config: dict = {"temperature": temperature, "maxOutputTokens": 4096}
    if response_schema:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = response_schema
    if config.THINKING_LEVEL:
        generation_config["thinkingConfig"] = {"thinkingLevel": config.THINKING_LEVEL}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": generation_config,
    }

    now = time.time()
    models = [m for m in config.CHAT_MODELS if _cooldown_until.get(m, 0) <= now]
    last_error: GeminiError | None = None
    for model in models or config.CHAT_MODELS:
        try:
            try:
                data = _post(f"models/{model}:generateContent", body, CHAT_TIMEOUT_SECONDS)
            except GeminiError as exc:
                if exc.status != 400 or "thinkingConfig" not in generation_config:
                    raise
                # This model doesn't accept a thinking level: ask again without it.
                generation_config.pop("thinkingConfig")
                data = _post(f"models/{model}:generateContent", body, CHAT_TIMEOUT_SECONDS)
        except GeminiError as exc:
            last_error = exc
            if exc.status is None or exc.status in RETRYABLE:
                print(f"[gemini] {model} unavailable ({exc}); trying the next chat model")
                _cooldown_until[model] = time.time() + COOLDOWN_SECONDS
                continue
            raise
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if text.strip():
            return text
        last_error = GeminiError(f"{model} returned an empty reply")
    raise last_error or GeminiError("no chat model configured")
