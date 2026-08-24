"""spaCy NER — supporting extraction for free-text columns only."""

from __future__ import annotations

from typing import Any

_NLP_CACHE: dict[str, Any] = {}

# GPE/LOC/ORG are useful for entity resolution; PERSON is PII — skipped by default.
_NER_LABELS = frozenset({"GPE", "LOC", "ORG", "FAC"})


def _load_nlp(model_name: str = "en_core_web_sm"):
    if model_name not in _NLP_CACHE:
        import spacy

        try:
            _NLP_CACHE[model_name] = spacy.load(model_name)
        except OSError:
            _NLP_CACHE[model_name] = None
    return _NLP_CACHE[model_name]


def extract_entities_from_text(
    text: str,
    *,
    model_name: str = "en_core_web_sm",
    skip_person: bool = True,
    min_length: int = 2,
) -> list[str]:
    """
    Extract mention strings from free text — NOT the primary resolver.

    Returns unique entity surface forms suitable for downstream cascade
    resolution. Returns [] when spaCy model unavailable or text too short.
    """
    if not text or len(str(text).strip()) < min_length:
        return []
    nlp = _load_nlp(model_name)
    if nlp is None:
        return []
    doc = nlp(str(text))
    labels = set(_NER_LABELS)
    if not skip_person:
        labels.add("PERSON")
    seen: set[str] = set()
    out: list[str] = []
    for ent in doc.ents:
        if ent.label_ not in labels:
            continue
        mention = ent.text.strip()
        if len(mention) < min_length or mention in seen:
            continue
        seen.add(mention)
        out.append(mention)
    return out
