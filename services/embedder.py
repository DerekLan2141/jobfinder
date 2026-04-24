"""
Gemini text-embedding-004 helpers.
Calls the REST API directly to avoid google-genai SDK routing issues.
Embeddings are 768-dimensional float vectors stored as JSON in the DB.
"""
import json
import os
import requests

_EMBED_MODEL = "embedding-001"
_EMBED_URL = (
    "https://generativelanguage.googleapis.com"
    f"/v1beta/models/{_EMBED_MODEL}:embedContent"
)
_BATCH_URL = (
    "https://generativelanguage.googleapis.com"
    f"/v1beta/models/{_EMBED_MODEL}:batchEmbedContents"
)
_BATCH_SIZE = 100


def embed_one(text: str) -> list[float]:
    api_key = os.getenv("GEMINI_API_KEY")
    resp = requests.post(
        _EMBED_URL,
        params={"key": api_key},
        json={"model": f"models/{_EMBED_MODEL}",
              "content": {"parts": [{"text": text}]}},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]["values"]


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    api_key  = os.getenv("GEMINI_API_KEY")
    all_vecs: list[list[float]] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        chunk = texts[i : i + _BATCH_SIZE]
        requests_body = [
            {"model": f"models/{_EMBED_MODEL}",
             "content": {"parts": [{"text": t}]}}
            for t in chunk
        ]
        resp = requests.post(
            _BATCH_URL,
            params={"key": api_key},
            json={"requests": requests_body},
            timeout=30,
        )
        resp.raise_for_status()
        for item in resp.json().get("embeddings", []):
            all_vecs.append(item["values"])

    return all_vecs


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [0, 1] between two embedding vectors."""
    import math
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def job_text(job) -> str:
    """Build the text we embed for a Job row."""
    return " ".join(filter(None, [
        job.title,
        job.company,
        job.location,
        job.description_snippet,
        job.matched_keywords,
    ]))


def load_embedding(json_str: str | None) -> list[float] | None:
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except Exception:
        return None


def dump_embedding(vec: list[float]) -> str:
    return json.dumps(vec)
