"""Generate original AI perfume overviews (summary + chips + detail lists).

Separate from mood scoring — does not modify mood_compute / review_scorer /
opinion_scorer.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pipeline.constants import (
    AI_OVERVIEW_MAX_ATTEMPTS,
    AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT,
    AI_OVERVIEW_REVIEW_CHAR_LIMIT,
    AI_OVERVIEW_VERBATIM_MIN_WORDS,
    LLM_MODEL,
    REVIEW_HELPFULNESS_WEIGHT_CAP,
)
from pipeline.llm_client import (
    LLMUnavailableError,
    generate_ai_overview,
    is_llm_configured,
)
from utils.db import supabase


def _helpfulness_weight(vote_yes: Any, vote_no: Any) -> float:
    """Same formula as mood compute — duplicated so we do not edit that module."""
    try:
        yes = int(vote_yes) if vote_yes is not None else 0
    except (TypeError, ValueError):
        yes = 0
    try:
        no = int(vote_no) if vote_no is not None else 0
    except (TypeError, ValueError):
        no = 0
    net = max(0, yes - no)
    return min(1.0 + math.log(1.0 + net), REVIEW_HELPFULNESS_WEIGHT_CAP)


def _norm_ws(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _as_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
    return out


def _parse_opinion_texts(perfume: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Return (pro_texts, con_texts) from perfumes.pros / cons JSON."""
    pros: List[str] = []
    cons: List[str] = []
    for field, bucket in (("pros", pros), ("cons", cons)):
        raw = perfume.get(field) or []
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, dict):
                text = (entry.get("opinion") or "").strip()
            elif isinstance(entry, str):
                text = entry.strip()
            else:
                continue
            if text:
                bucket.append(text)
    return pros, cons


def _fetch_reviews(perfume_id: str) -> List[Dict[str, Any]]:
    response = (
        supabase.table("reviews")
        .select("id,sentiment,content_text,vote_yes,vote_no")
        .eq("perfume_id", perfume_id)
        .execute()
    )
    rows = response.data or []
    return [r for r in rows if (r.get("content_text") or "").strip()]


def _stratified_sample(
    reviews: List[Dict[str, Any]],
    *,
    per_side: int,
    char_limit: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Top-N by helpfulness within positive and negative sentiment."""
    by_side: Dict[str, List[Dict[str, Any]]] = {"positive": [], "negative": []}
    for r in reviews:
        sent = (r.get("sentiment") or "").strip().lower()
        if sent not in by_side:
            continue
        by_side[sent].append(r)

    selected: List[Dict[str, Any]] = []
    counts = {"positive": 0, "negative": 0}
    for side in ("positive", "negative"):
        ranked = sorted(
            by_side[side],
            key=lambda r: _helpfulness_weight(r.get("vote_yes"), r.get("vote_no")),
            reverse=True,
        )[:per_side]
        counts[side] = len(ranked)
        for r in ranked:
            text = (r.get("content_text") or "").strip()
            if len(text) > char_limit:
                text = text[:char_limit].rstrip() + "…"
            selected.append(
                {
                    "id": r.get("id"),
                    "sentiment": side,
                    "content_text": text,
                    "weight": _helpfulness_weight(r.get("vote_yes"), r.get("vote_no")),
                }
            )
    return selected, counts


def _is_polarizing(perfume_id: str) -> bool:
    """Reuse last moods/compute gated_out if present."""
    try:
        response = (
            supabase.table("perfume_mood_scores")
            .select("gated_out")
            .eq("perfume_id", perfume_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if rows and rows[0].get("gated_out"):
            return True
    except Exception:
        pass
    return False


def _source_corpus(
    pro_texts: Sequence[str],
    con_texts: Sequence[str],
    selected_reviews: Sequence[Dict[str, Any]],
) -> List[str]:
    corpus = [_norm_ws(t) for t in list(pro_texts) + list(con_texts)]
    for r in selected_reviews:
        corpus.append(_norm_ws(r.get("content_text") or ""))
    return [c for c in corpus if c]


def _scrub_identity(text: str, identity_phrases: Sequence[str]) -> str:
    """Remove perfume name/brand so identity mentions are not treated as plagiarism."""
    out = _norm_ws(text)
    for phrase in sorted((_norm_ws(p) for p in identity_phrases if p), key=len, reverse=True):
        if phrase:
            out = out.replace(phrase, " ")
    return _norm_ws(out)


def _has_verbatim_copy(
    phrase: str,
    corpus: Sequence[str],
    min_words: int,
    *,
    identity_phrases: Sequence[str] = (),
) -> bool:
    words = _scrub_identity(phrase, identity_phrases).split()
    if len(words) < min_words:
        return False
    scrubbed_corpus = [_scrub_identity(src, identity_phrases) for src in corpus]
    joined = " ".join(words)
    for src in scrubbed_corpus:
        if joined and joined in src:
            return True
    for i in range(len(words) - min_words + 1):
        window = " ".join(words[i : i + min_words])
        for src in scrubbed_corpus:
            if window in src:
                return True
    return False


def _token_set(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _chip_covered_soft(chip: str, detail_items: Sequence[str]) -> bool:
    """Soft: chip tokens largely appear somewhere in the detail list."""
    chip_toks = _token_set(chip)
    if not chip_toks:
        return True
    blob = " ".join(detail_items).lower()
    hits = sum(1 for t in chip_toks if t in blob)
    return hits >= max(1, (len(chip_toks) + 1) // 2)


def _validate_payload(
    raw: Dict[str, Any],
    corpus: Sequence[str],
    *,
    identity_phrases: Sequence[str] = (),
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary missing or empty")
        summary = ""

    pros = _as_str_list(raw.get("pros"))
    cons = _as_str_list(raw.get("cons"))
    pros_list = _as_str_list(raw.get("pros_list"))
    cons_list = _as_str_list(raw.get("cons_list"))

    if not (3 <= len(pros) <= 5):
        errors.append(f"pros length {len(pros)} not in 3–5")
    if not (3 <= len(cons) <= 5):
        errors.append(f"cons length {len(cons)} not in 3–5")
    if not (6 <= len(pros_list) <= 10):
        errors.append(f"pros_list length {len(pros_list)} not in 6–10")
    if not (6 <= len(cons_list) <= 10):
        errors.append(f"cons_list length {len(cons_list)} not in 6–10")

    for label, items in (
        ("pros", pros),
        ("cons", cons),
        ("pros_list", pros_list),
        ("cons_list", cons_list),
        ("summary", [summary] if summary else []),
    ):
        for item in items:
            if _has_verbatim_copy(
                item,
                corpus,
                AI_OVERVIEW_VERBATIM_MIN_WORDS,
                identity_phrases=identity_phrases,
            ):
                errors.append(f"verbatim copy in {label}: {item[:80]}…")

    soft_warnings: List[str] = []
    for chip in pros:
        if not _chip_covered_soft(chip, pros_list):
            soft_warnings.append(f"pros chip weakly covered in pros_list: {chip}")
    for chip in cons:
        if not _chip_covered_soft(chip, cons_list):
            soft_warnings.append(f"cons chip weakly covered in cons_list: {chip}")

    if errors:
        return None, errors + soft_warnings

    return (
        {
            "summary": summary.strip(),
            "pros": pros,
            "cons": cons,
            "pros_list": pros_list,
            "cons_list": cons_list,
            "_soft_warnings": soft_warnings,
        },
        soft_warnings,
    )


def _get_existing(perfume_id: str) -> Optional[Dict[str, Any]]:
    response = (
        supabase.table("perfume_ai_overviews")
        .select("*")
        .eq("perfume_id", perfume_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def get_ai_overview(perfume_id: str) -> Dict[str, Any]:
    """Public read helper — never generates."""
    row = _get_existing(perfume_id)
    if not row:
        return {"perfume_id": perfume_id, "generated": False}
    return {
        "perfume_id": perfume_id,
        "generated": True,
        "summary": row.get("summary"),
        "pros": row.get("pros") or [],
        "cons": row.get("cons") or [],
        "pros_list": row.get("pros_list") or [],
        "cons_list": row.get("cons_list") or [],
        "model": row.get("model"),
        "input_review_count": row.get("input_review_count"),
        "input_opinion_count": row.get("input_opinion_count"),
        "generated_at": row.get("generated_at"),
    }


def generate_overview(perfume_id: str, *, force: bool = False) -> Dict[str, Any]:
    """
    Auth pipeline: select inputs → LLM → validate → upsert.
    Skips LLM when a row exists and force is False.
    """
    perfume_resp = (
        supabase.table("perfumes")
        .select("id,name,brand,pros,cons")
        .eq("id", perfume_id)
        .limit(1)
        .execute()
    )
    perfume_rows = perfume_resp.data or []
    if not perfume_rows:
        raise ValueError(f"Perfume not found: {perfume_id}")
    perfume = perfume_rows[0]

    existing = _get_existing(perfume_id)
    if existing and not force:
        return {
            "status": "skipped",
            "reason": "already_generated",
            "force": False,
            "overview": get_ai_overview(perfume_id),
        }

    if not is_llm_configured():
        raise LLMUnavailableError("LLM_BASE_URL is not set")

    pro_texts, con_texts = _parse_opinion_texts(perfume)
    reviews = _fetch_reviews(perfume_id)
    selected, side_counts = _stratified_sample(
        reviews,
        per_side=AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT,
        char_limit=AI_OVERVIEW_REVIEW_CHAR_LIMIT,
    )

    if not selected and not pro_texts and not con_texts:
        raise ValueError(
            "insufficient_data: no reviews and empty pros/cons — refusing to generate"
        )

    opinion_lines = [f"[pro] {t}" for t in pro_texts] + [f"[con] {t}" for t in con_texts]
    opinions_block = "\n".join(opinion_lines) if opinion_lines else "(none)"

    review_lines = [f"[{r['sentiment']}] {r['content_text']}" for r in selected]
    reviews_block = "\n\n".join(review_lines) if review_lines else "(none)"

    corpus = _source_corpus(pro_texts, con_texts, selected)
    polarizing = _is_polarizing(perfume_id)
    identity_phrases = [
        perfume.get("name") or "",
        perfume.get("brand") or "",
        " ".join(
            x for x in [(perfume.get("name") or ""), (perfume.get("brand") or "")] if x
        ),
    ]

    last_errors: List[str] = []
    validated: Optional[Dict[str, Any]] = None
    best_hard_ok: Optional[Dict[str, Any]] = None
    for attempt in range(1, AI_OVERVIEW_MAX_ATTEMPTS + 1):
        raw = generate_ai_overview(
            perfume_name=perfume.get("name") or "Unknown",
            brand=perfume.get("brand"),
            opinions_block=opinions_block,
            reviews_block=reviews_block,
            polarizing=polarizing,
        )
        validated, msgs = _validate_payload(
            raw, corpus, identity_phrases=identity_phrases
        )
        if validated is not None:
            best_hard_ok = validated
            # Soft chip coverage: one extra retry then accept hard-valid result
            if validated.get("_soft_warnings") and attempt == 1 and attempt < AI_OVERVIEW_MAX_ATTEMPTS:
                last_errors = list(validated["_soft_warnings"])
                continue
            break
        last_errors = msgs
        validated = None

    if validated is None:
        validated = best_hard_ok
    if validated is None:
        raise ValueError(
            "ai_overview_validation_failed after "
            f"{AI_OVERVIEW_MAX_ATTEMPTS} attempts: {'; '.join(last_errors[:8])}"
        )

    soft_warnings = validated.pop("_soft_warnings", [])
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "perfume_id": perfume_id,
        "summary": validated["summary"],
        "pros": validated["pros"],
        "cons": validated["cons"],
        "pros_list": validated["pros_list"],
        "cons_list": validated["cons_list"],
        "model": LLM_MODEL,
        "input_review_count": len(selected),
        "input_opinion_count": len(pro_texts) + len(con_texts),
        "generated_at": now,
    }
    supabase.table("perfume_ai_overviews").upsert(
        row, on_conflict="perfume_id"
    ).execute()

    return {
        "status": "generated",
        "force": force,
        "polarizing_hint": polarizing,
        "selection": {
            "reviews_positive": side_counts["positive"],
            "reviews_negative": side_counts["negative"],
            "reviews_total_available": len(reviews),
            "opinions_pros": len(pro_texts),
            "opinions_cons": len(con_texts),
            "max_per_sentiment": AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT,
            "review_char_limit": AI_OVERVIEW_REVIEW_CHAR_LIMIT,
        },
        "soft_warnings": soft_warnings,
        "overview": get_ai_overview(perfume_id),
    }
