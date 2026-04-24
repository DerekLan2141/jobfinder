"""
Gemini text-embedding-004 helpers.
Embeddings are 768-dimensional float vectors stored as JSON in the DB.
"""
import json
import os


EMBED_MODEL = "text-embedding-004"
_BATCH_SIZE = 100

_gemini_client = None


def _client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


def embed_one(text: str) -> list[float]:
    result = _client().models.embed_content(model=EMBED_MODEL, contents=text)
    return list(result.embeddings[0].values)


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _client()
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        chunk = texts[i : i + _BATCH_SIZE]
        result = client.models.embed_content(model=EMBED_MODEL, contents=chunk)
        all_vecs.extend(list(e.values) for e in result.embeddings)
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
