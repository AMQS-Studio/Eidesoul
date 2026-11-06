"""One embedder for the whole engine: multilingual MiniLM on CPU, loaded once.

Memory, affect prototypes, and privacy evidence share the same 384-dim multilingual space,
so a single required dependency serves all three. When the model cannot load every caller
falls back to its keyword path; nothing raises.
"""


import logging
import math
from functools import cache

log = logging.getLogger(__name__)

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
WARNED: set[str] = set()


def _warn_once(reason: str, message: str, *args) -> None:
    if reason in WARNED:
        return
    WARNED.add(reason)
    log.warning(message, *args)


def _cached(model: str) -> bool:
    """True when the weights are already on disk, so window startup makes no network call."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    try:
        found = try_to_load_from_cache(model, "modules.json")
    except Exception:
        return False
    return isinstance(found, str)


@cache
def encoder(model: str = MODEL):
    """The shared SentenceTransformer, or `None` when the weights cannot be loaded."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        _warn_once("import", "embed: sentence-transformers is not installed, using keywords")
        return None
    try:
        return SentenceTransformer(model, device="cpu", local_files_only=_cached(model))
    except Exception:
        _warn_once("load", "embed: could not load %s, using keywords", model)
        return None


def cosine(left, right) -> float:
    """Cosine similarity; a zero vector scores 0 instead of dividing by zero."""
    norm_left = math.sqrt(sum(float(value) ** 2 for value in left))
    norm_right = math.sqrt(sum(float(value) ** 2 for value in right))
    if not norm_left or not norm_right:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
    return dot / (norm_left * norm_right)


def encode(texts: list[str], *, model: str = MODEL) -> list[list[float]]:
    """One vector per non-empty text; `[]` when the embedder is unavailable."""
    body = [str(text or "").strip() for text in texts]
    if not any(body):
        return []
    found = encoder(model)
    if found is None:
        return []
    try:
        found_vectors = found.encode(body, convert_to_numpy=True, show_progress_bar=False)
    except Exception:
        _warn_once("encode", "embed: encode failed, using keywords")
        return []
    return [[float(value) for value in row] for row in found_vectors]
