"""Score Fragrantica pros/cons into perfume_opinion_scores (character-filtered)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from pipeline.constants import (
    ALL_SCORE_KEYS,
    GATE_KEYS,
    LEXICON_BLEND_KEYS,
    LEXICON_MIN_MATCHED_WORDS_OPINIONS,
    MOOD_AXES,
)
from pipeline.lexicon_scorer import score_review_lexicon
from pipeline.llm_client import (
    LLMUnavailableError,
    is_llm_configured,
    score_opinion_text,
    score_opinions_batch,
)
from utils.db import supabase


def _label_map(table: str, keys: tuple) -> Dict[str, str]:
    response = supabase.table(table).select("key,labels").in_("key", list(keys)).execute()
    out: Dict[str, str] = {}
    for row in response.data or []:
        labels = row.get("labels") or {}
        en = (labels.get("en") or {}).get("label") or row["key"]
        out[row["key"]] = en
    return out


def _parse_opinion_items(perfume: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for opinion_type, field in (("pro", "pros"), ("con", "cons")):
        raw = perfume.get(field) or []
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            text = (entry.get("opinion") or "").strip()
            if not text:
                continue
            try:
                fscore = int(entry.get("score") or 0)
            except (TypeError, ValueError):
                fscore = 0
            items.append(
                {
                    "opinion_type": opinion_type,
                    "opinion_text": text,
                    "fragrantica_score": max(0, fscore),
                }
            )
    return items


def _already_scored_opinion_texts(perfume_id: str) -> Set[str]:
    """Opinion texts that already have a full LLM axis set."""
    response = (
        supabase.table("perfume_opinion_scores")
        .select("opinion_text,axis_key")
        .eq("perfume_id", perfume_id)
        .eq("method", "llm")
        .execute()
    )
    by_text: Dict[str, Set[str]] = {}
    for row in response.data or []:
        by_text.setdefault(row["opinion_text"], set()).add(row["axis_key"])
    needed = set(ALL_SCORE_KEYS)
    return {text for text, keys in by_text.items() if needed.issubset(keys)}


def clear_opinion_scores_for_perfume(perfume_id: str) -> int:
    response = (
        supabase.table("perfume_opinion_scores")
        .delete()
        .eq("perfume_id", perfume_id)
        .execute()
    )
    return len(response.data or [])


def score_opinions(
    *,
    perfume_id: str,
    rescore: bool = False,
) -> Dict[str, Any]:
    if not perfume_id:
        raise ValueError("perfume_id is required")

    if not is_llm_configured():
        return {
            "status": "skipped",
            "message": "LLM not configured",
            "scored": 0,
            "skipped_irrelevant": 0,
            "lexicon_written": 0,
            "errors": [],
        }

    perfume_resp = (
        supabase.table("perfumes")
        .select("id,pros,cons")
        .eq("id", perfume_id)
        .limit(1)
        .execute()
    )
    if not perfume_resp.data:
        raise ValueError(f"Perfume not found: {perfume_id}")

    perfume = perfume_resp.data[0]
    items = _parse_opinion_items(perfume)

    cleared = 0
    if rescore:
        cleared = clear_opinion_scores_for_perfume(perfume_id)
        already: Set[str] = set()
    else:
        already = _already_scored_opinion_texts(perfume_id)

    axis_labels = _label_map("mood_axes", MOOD_AXES)
    gate_labels = _label_map("quality_gates", GATE_KEYS)
    now = datetime.now(timezone.utc).isoformat()

    scored = 0
    skipped_irrelevant = 0
    skipped_already = 0
    lexicon_written = 0
    errors: List[Dict[str, str]] = []

    to_score: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        text = item["opinion_text"]
        if text in already:
            skipped_already += 1
        else:
            to_score.append({"id": str(idx), **item})

    if to_score:
        batch_results: Dict[str, Dict[str, Any]] = {}
        try:
            batch_results = score_opinions_batch(
                to_score,
                axis_labels=axis_labels,
                gate_labels=gate_labels,
            )
        except LLMUnavailableError as exc:
            errors.append({"error": str(exc)})
        except Exception as exc:
            errors.append({"error": str(exc)})

        for item in to_score:
            item_id = item["id"]
            text = item["opinion_text"]
            if item_id in batch_results:
                result = batch_results[item_id]
                if not result.get("character_relevant"):
                    skipped_irrelevant += 1
                    continue

                llm_rows = [
                    {
                        "perfume_id": perfume_id,
                        "opinion_type": item["opinion_type"],
                        "opinion_text": text,
                        "fragrantica_score": item["fragrantica_score"],
                        "axis_key": key,
                        "score": result[key],
                        "method": "llm",
                        "computed_at": now,
                    }
                    for key in ALL_SCORE_KEYS
                ]
                supabase.table("perfume_opinion_scores").upsert(
                    llm_rows,
                    on_conflict="perfume_id,opinion_text,axis_key,method",
                ).execute()
                scored += 1

                lex = score_review_lexicon(text)
                if lex and int(lex["matched_words"]) >= LEXICON_MIN_MATCHED_WORDS_OPINIONS:
                    lex_rows = [
                        {
                            "perfume_id": perfume_id,
                            "opinion_type": item["opinion_type"],
                            "opinion_text": text,
                            "fragrantica_score": item["fragrantica_score"],
                            "axis_key": key,
                            "score": round(float(lex[key]), 2),
                            "method": "lexicon",
                            "computed_at": now,
                        }
                        for key in LEXICON_BLEND_KEYS
                    ]
                    supabase.table("perfume_opinion_scores").upsert(
                        lex_rows,
                        on_conflict="perfume_id,opinion_text,axis_key,method",
                    ).execute()
                    lexicon_written += 1

    return {
        "status": "success" if scored or skipped_irrelevant or not errors else "error",
        "message": (
            f"Scored {scored} opinion(s), skipped_irrelevant={skipped_irrelevant}, "
            f"lexicon={lexicon_written}"
        ),
        "perfume_id": perfume_id,
        "scored": scored,
        "skipped_irrelevant": skipped_irrelevant,
        "skipped_already": skipped_already,
        "lexicon_written": lexicon_written,
        "total_items": len(items),
        "rescore": rescore,
        "cleared": cleared,
        "errors": errors,
    }
