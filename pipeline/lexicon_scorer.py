"""NRC-VAD lexicon load + per-review valence/dominance scoring."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from pipeline.constants import (
    LEXICON_PATH,
    LEXICON_SOURCE,
    LEXICON_UPSERT_BATCH,
)
from utils.db import supabase

_TOKEN_RE = re.compile(r"[a-zA-Z']+")

# word -> (valence_0_100, dominance_0_100)
_LEXICON_CACHE: Optional[Dict[str, Tuple[float, float]]] = None


def rescale_vad(raw: float) -> float:
    """NRC raw −1..+1 → 0..100."""
    return max(0.0, min(100.0, (float(raw) + 1.0) * 50.0))


def clear_lexicon_cache() -> None:
    global _LEXICON_CACHE
    _LEXICON_CACHE = None


def _parse_lexicon_file(path: str) -> Dict[str, Tuple[float, float, float]]:
    """
    Parse NRC-VAD TSV. Unigrams only (skip terms with spaces).
    Returns word -> (valence, arousal, dominance) as raw NRC −1..+1.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Lexicon file not found: {path}")

    out: Dict[str, Tuple[float, float, float]] = {}
    with p.open(encoding="utf-8") as fh:
        header = fh.readline()
        if not header or "valence" not in header.lower():
            raise ValueError(f"Unexpected lexicon header in {path}: {header!r}")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            term = parts[0].strip()
            if not term or " " in term:
                continue
            try:
                v = max(-1.0, min(1.0, float(parts[1])))
                a = max(-1.0, min(1.0, float(parts[2])))
                d = max(-1.0, min(1.0, float(parts[3])))
            except ValueError:
                continue
            out[term.lower()] = (v, a, d)
    return out


def load_lexicon(
    path: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """
    Upsert unigram NRC-VAD rows into emotion_lexicon (raw −1..+1).
    Returns number of rows attempted (file unigram count).
    """
    path = path or LEXICON_PATH
    source = source or LEXICON_SOURCE
    if not os.path.isabs(path):
        repo_root = Path(__file__).resolve().parent.parent
        path = str(repo_root / path)

    parsed = _parse_lexicon_file(path)
    rows = [
        {
            "word": word,
            "language": "en",
            "valence": vals[0],
            "arousal": vals[1],
            "dominance": vals[2],
            "source": source,
        }
        for word, vals in parsed.items()
    ]

    for i in range(0, len(rows), LEXICON_UPSERT_BATCH):
        chunk = rows[i : i + LEXICON_UPSERT_BATCH]
        supabase.table("emotion_lexicon").upsert(
            chunk,
            on_conflict="word,language,source",
        ).execute()

    clear_lexicon_cache()
    global _LEXICON_CACHE
    # Cache already rescaled to 0–100 for scoring
    _LEXICON_CACHE = {
        w: (rescale_vad(v), rescale_vad(d)) for w, (v, _a, d) in parsed.items()
    }
    return len(rows)


def get_lexicon_map() -> Dict[str, Tuple[float, float]]:
    """In-memory word -> (valence, dominance) on 0–100 scale. Lazy-loads from DB."""
    global _LEXICON_CACHE
    if _LEXICON_CACHE is not None:
        return _LEXICON_CACHE

    cache: Dict[str, Tuple[float, float]] = {}
    page_size = 1000
    offset = 0
    while True:
        response = (
            supabase.table("emotion_lexicon")
            .select("word,valence,dominance")
            .eq("language", "en")
            .eq("source", LEXICON_SOURCE)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = response.data or []
        if not batch:
            break
        for row in batch:
            word = (row.get("word") or "").lower()
            if not word:
                continue
            if row.get("valence") is None or row.get("dominance") is None:
                continue
            raw_v = float(row["valence"])
            raw_d = float(row["dominance"])
            # Support both raw (−1..+1) and legacy 0–100 storage
            if -1.5 <= raw_v <= 1.5 and -1.5 <= raw_d <= 1.5:
                cache[word] = (rescale_vad(raw_v), rescale_vad(raw_d))
            else:
                cache[word] = (
                    max(0.0, min(100.0, raw_v)),
                    max(0.0, min(100.0, raw_d)),
                )
        if len(batch) < page_size:
            break
        offset += page_size

    _LEXICON_CACHE = cache
    return cache


def score_review_lexicon(text: str) -> Optional[Dict[str, float]]:
    """
    Tokenize review, average matched NRC-VAD valence/dominance (0–100).
    Returns None if zero matches. Always includes matched_words count.
    """
    if not (text or "").strip():
        return None

    lex = get_lexicon_map()
    if not lex:
        return None

    tokens = _TOKEN_RE.findall(text.lower())
    vals = []
    doms = []
    for tok in tokens:
        hit = lex.get(tok)
        if hit is None:
            continue
        vals.append(hit[0])
        doms.append(hit[1])

    matched = len(vals)
    if matched == 0:
        return None

    return {
        "valence": sum(vals) / matched,
        "dominance": sum(doms) / matched,
        "matched_words": float(matched),
    }
