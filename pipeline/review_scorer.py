"""Score reviews via OmniRoute LLM + NRC-VAD lexicon → review_axis_scores."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from pipeline.constants import (
    ALL_SCORE_KEYS,
    DEFAULT_SCORE_LIMIT,
    GATE_KEYS,
    LEXICON_BLEND_KEYS,
    LEXICON_MIN_MATCHED_WORDS,
    MAX_SCORE_LIMIT,
    MOOD_AXES,
)
from pipeline.lexicon_scorer import score_review_lexicon
from pipeline.llm_client import LLMUnavailableError, is_llm_configured, score_review_text
from utils.db import supabase

_DELETE_CHUNK = 100


def _label_map(table: str, keys: tuple) -> Dict[str, str]:
    response = supabase.table(table).select("key,labels").in_("key", list(keys)).execute()
    out: Dict[str, str] = {}
    for row in response.data or []:
        labels = row.get("labels") or {}
        en = (labels.get("en") or {}).get("label") or row["key"]
        out[row["key"]] = en
    return out


def _review_ids_for_perfume(perfume_id: str) -> List[str]:
    response = (
        supabase.table("reviews")
        .select("id")
        .eq("perfume_id", perfume_id)
        .execute()
    )
    return [row["id"] for row in (response.data or [])]


def clear_scores_for_perfume(perfume_id: str) -> Dict[str, int]:
    """Delete llm + lexicon review_axis_scores for one perfume's reviews."""
    review_ids = _review_ids_for_perfume(perfume_id)
    if not review_ids:
        return {"reviews": 0, "score_rows_deleted": 0}

    deleted = 0
    for i in range(0, len(review_ids), _DELETE_CHUNK):
        chunk = review_ids[i : i + _DELETE_CHUNK]
        response = (
            supabase.table("review_axis_scores")
            .delete()
            .in_("review_id", chunk)
            .execute()
        )
        deleted += len(response.data or [])
    return {"reviews": len(review_ids), "score_rows_deleted": deleted}


# Back-compat alias
clear_llm_scores_for_perfume = clear_scores_for_perfume


def _persist_lexicon_scores(review_id: str, text: str, now: str) -> Optional[int]:
    """
    Score + upsert lexicon valence/dominance if matched_words >= MIN.
    Returns matched_words or None if nothing written.
    """
    result = score_review_lexicon(text)
    if not result:
        return None
    matched = int(result["matched_words"])
    if matched < LEXICON_MIN_MATCHED_WORDS:
        return matched  # below threshold — do not persist

    rows = [
        {
            "review_id": review_id,
            "axis_key": key,
            "score": round(float(result[key]), 2),
            "method": "lexicon",
            "computed_at": now,
        }
        for key in LEXICON_BLEND_KEYS
    ]
    supabase.table("review_axis_scores").upsert(
        rows,
        on_conflict="review_id,axis_key,method",
    ).execute()
    return matched


def _already_scored_review_ids(
    *,
    perfume_id: Optional[str] = None,
    method: str = "llm",
) -> Set[str]:
    """Reviews that already have a full set of llm axis scores."""
    scope_ids: Optional[Set[str]] = None
    if perfume_id:
        scope_ids = set(_review_ids_for_perfume(perfume_id))
        if not scope_ids:
            return set()

    response = (
        supabase.table("review_axis_scores")
        .select("review_id,axis_key")
        .eq("method", method)
        .execute()
    )
    by_review: Dict[str, Set[str]] = {}
    for row in response.data or []:
        rid = row["review_id"]
        if scope_ids is not None and rid not in scope_ids:
            continue
        by_review.setdefault(rid, set()).add(row["axis_key"])
    needed = set(ALL_SCORE_KEYS)
    return {rid for rid, keys in by_review.items() if needed.issubset(keys)}


def _reviews_needing_lexicon_backfill(
    *,
    perfume_id: Optional[str] = None,
    limit: int = DEFAULT_SCORE_LIMIT,
) -> List[Dict[str, Any]]:
    """Full LLM set, but missing lexicon valence+dominance."""
    limit = max(1, min(int(limit), MAX_SCORE_LIMIT))
    llm_done = _already_scored_review_ids(perfume_id=perfume_id, method="llm")
    if not llm_done:
        return []

    response = (
        supabase.table("review_axis_scores")
        .select("review_id,axis_key")
        .eq("method", "lexicon")
        .in_("axis_key", list(LEXICON_BLEND_KEYS))
        .execute()
    )
    lex_by: Dict[str, Set[str]] = {}
    for row in response.data or []:
        lex_by.setdefault(row["review_id"], set()).add(row["axis_key"])
    lex_complete = {
        rid for rid, keys in lex_by.items() if set(LEXICON_BLEND_KEYS).issubset(keys)
    }

    need_ids = [rid for rid in llm_done if rid not in lex_complete]
    if not need_ids:
        return []

    query = (
        supabase.table("reviews")
        .select("id,perfume_id,content_text,sentiment")
        .in_("id", need_ids[: limit * 5])
    )
    if perfume_id:
        query = query.eq("perfume_id", perfume_id)
    rows = []
    for row in query.execute().data or []:
        if not (row.get("content_text") or "").strip():
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def fetch_unscored_reviews(
    *,
    perfume_id: Optional[str] = None,
    limit: int = DEFAULT_SCORE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_SCORE_LIMIT))
    scored = _already_scored_review_ids(perfume_id=perfume_id)

    query = (
        supabase.table("reviews")
        .select("id,perfume_id,content_text,sentiment")
        .order("review_date", desc=True)
        .limit(limit * 5)
    )
    if perfume_id:
        query = query.eq("perfume_id", perfume_id)
    response = query.execute()
    rows = []
    for row in response.data or []:
        if row["id"] in scored:
            continue
        if not (row.get("content_text") or "").strip():
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def score_reviews(
    *,
    perfume_id: Optional[str] = None,
    limit: int = DEFAULT_SCORE_LIMIT,
    rescore: bool = False,
) -> Dict[str, Any]:
    if rescore and not perfume_id:
        raise ValueError("rescore=true requires perfume_id (refuses global score wipe)")

    if not is_llm_configured():
        return {
            "status": "skipped",
            "message": "LLM not configured (set LLM_BASE_URL / LLM_API_KEY)",
            "scored": 0,
            "lexicon_written": 0,
            "skipped": 0,
            "errors": [],
            "remaining_estimate": None,
            "rescore": rescore,
            "cleared": None,
        }

    cleared: Optional[Dict[str, int]] = None
    if rescore:
        cleared = clear_scores_for_perfume(perfume_id)  # type: ignore[arg-type]

    reviews = fetch_unscored_reviews(perfume_id=perfume_id, limit=limit)
    axis_labels = _label_map("mood_axes", MOOD_AXES)
    gate_labels = _label_map("quality_gates", GATE_KEYS)

    scored = 0
    lexicon_written = 0
    errors: List[Dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat()

    for review in reviews:
        llm_ok = False
        try:
            scores = score_review_text(
                review["content_text"],
                sentiment=review.get("sentiment"),
                axis_labels=axis_labels,
                gate_labels=gate_labels,
            )
            rows = [
                {
                    "review_id": review["id"],
                    "axis_key": key,
                    "score": scores[key],
                    "method": "llm",
                    "computed_at": now,
                }
                for key in ALL_SCORE_KEYS
            ]
            supabase.table("review_axis_scores").upsert(
                rows,
                on_conflict="review_id,axis_key,method",
            ).execute()
            scored += 1
            llm_ok = True
        except LLMUnavailableError as exc:
            errors.append({"review_id": review["id"], "error": str(exc)})
            # Still attempt lexicon for this review, then stop batch
            matched = _persist_lexicon_scores(review["id"], review["content_text"], now)
            if matched is not None and matched >= LEXICON_MIN_MATCHED_WORDS:
                lexicon_written += 1
            break
        except Exception as exc:
            errors.append({"review_id": review["id"], "error": str(exc)})

        matched = _persist_lexicon_scores(review["id"], review["content_text"], now)
        if matched is not None and matched >= LEXICON_MIN_MATCHED_WORDS:
            lexicon_written += 1
        elif not llm_ok:
            # neither persisted usefully beyond error log
            pass

    remaining = None
    try:
        q = supabase.table("reviews").select("id", count="exact")
        if perfume_id:
            q = q.eq("perfume_id", perfume_id)
        total = q.execute().count or 0
        remaining = max(0, total - len(_already_scored_review_ids(perfume_id=perfume_id)))
    except Exception:
        pass

    return {
        "status": "success" if scored or lexicon_written or not errors else "error",
        "message": (
            f"Rescored {scored} review(s), lexicon rows for {lexicon_written}"
            if rescore
            else f"Scored {scored} review(s), lexicon rows for {lexicon_written}"
        ),
        "scored": scored,
        "lexicon_written": lexicon_written,
        "skipped": 0,
        "errors": errors,
        "remaining_estimate": remaining,
        "batch_size": len(reviews),
        "rescore": rescore,
        "cleared": cleared,
    }


def backfill_lexicon_scores(
    *,
    perfume_id: Optional[str] = None,
    limit: int = DEFAULT_SCORE_LIMIT,
) -> Dict[str, Any]:
    """Add lexicon valence/dominance for reviews that already have full LLM scores."""
    reviews = _reviews_needing_lexicon_backfill(perfume_id=perfume_id, limit=limit)
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    skipped_low_match = 0
    errors: List[Dict[str, str]] = []

    for review in reviews:
        try:
            matched = _persist_lexicon_scores(review["id"], review["content_text"], now)
            if matched is None:
                skipped_low_match += 1
            elif matched < LEXICON_MIN_MATCHED_WORDS:
                skipped_low_match += 1
            else:
                written += 1
        except Exception as exc:
            errors.append({"review_id": review["id"], "error": str(exc)})

    return {
        "status": "success" if written or not errors else "error",
        "message": f"Backfilled lexicon for {written} review(s)",
        "written": written,
        "skipped_low_match": skipped_low_match,
        "errors": errors,
        "batch_size": len(reviews),
        "remaining_estimate": None,
    }
