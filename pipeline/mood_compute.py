"""Compute perfume_mood_scores from note priors + review posteriors + gates."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pipeline.constants import (
    DISGUST_GATE,
    GATE_KEYS,
    LEXICON_BLEND_KEYS,
    LEXICON_BLEND_WEIGHT_LLM,
    LEXICON_DIVERGENCE_THRESHOLD,
    LLM_ONLY_MOOD_AXES,
    MOOD_AXES,
    PROS_CONS_WEIGHT_CAP,
    REVIEW_BLEND_THRESHOLD,
    REVIEW_HELPFULNESS_WEIGHT_CAP,
    VALENCE_FLOOR,
)
from utils.db import supabase


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _helpfulness_weight(vote_yes: Any, vote_no: Any) -> float:
    """
    Community credibility weight — independent of sentiment/score.
    weight = min(1 + ln(1 + max(0, yes - no)), CAP); nulls → 1.
    """
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


def _opinion_weight(fragrantica_score: Any) -> float:
    """Weight from Fragrantica's pre-computed pros/cons ranking score."""
    try:
        raw = int(fragrantica_score) if fragrantica_score is not None else 0
    except (TypeError, ValueError):
        raw = 0
    net = max(0, raw)
    return min(1.0 + math.log(1.0 + net), PROS_CONS_WEIGHT_CAP)


def _weighted_mean(pairs: List[Tuple[float, float]]) -> float:
    """pairs of (score, weight). Empty → 0.0."""
    if not pairs:
        return 0.0
    num = sum(s * w for s, w in pairs)
    den = sum(w for _, w in pairs)
    if den <= 0:
        return 0.0
    return num / den


def _load_alias_map() -> Dict[str, str]:
    response = supabase.table("note_aliases").select("raw_text,note_category_key").execute()
    return {
        _norm(row["raw_text"]): row["note_category_key"]
        for row in (response.data or [])
        if row.get("raw_text") and row.get("note_category_key")
    }


def _load_category_weights() -> Dict[str, Dict[str, float]]:
    response = (
        supabase.table("note_category_axis_weights")
        .select("note_category_key,axis_key,weight")
        .execute()
    )
    out: Dict[str, Dict[str, float]] = {}
    for row in response.data or []:
        out.setdefault(row["note_category_key"], {})[row["axis_key"]] = float(row["weight"])
    return out


def _load_mood_label_weights() -> Dict[str, Dict[str, float]]:
    response = supabase.table("mood_label_weights").select("mood_key,axis_key,weight").execute()
    out: Dict[str, Dict[str, float]] = {}
    for row in response.data or []:
        out.setdefault(row["mood_key"], {})[row["axis_key"]] = float(row["weight"])
    return out


def _note_prior(
    perfume: Dict[str, Any],
    alias_map: Dict[str, str],
    cat_weights: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Weighted average of category→axis weights using accord_breakdown percents."""
    breakdown = perfume.get("accord_breakdown") or {}
    if not isinstance(breakdown, dict) or not breakdown:
        items: List[Tuple[str, float]] = []
        for field in ("notes_top", "notes_middle", "notes_base"):
            for note in perfume.get(field) or []:
                cat = alias_map.get(_norm(str(note)))
                if cat:
                    items.append((cat, 1.0))
        if not items:
            return {axis: 0.0 for axis in MOOD_AXES}
        breakdown = {}
        for cat, _ in items:
            breakdown[cat] = breakdown.get(cat, 0) + 1
        totals = {axis: 0.0 for axis in MOOD_AXES}
        weight_sum = 0.0
        for cat, w in breakdown.items():
            cw = cat_weights.get(cat) or {}
            weight_sum += float(w)
            for axis in MOOD_AXES:
                totals[axis] += float(w) * float(cw.get(axis, 0.0))
        if weight_sum <= 0:
            return {axis: 0.0 for axis in MOOD_AXES}
        return {axis: totals[axis] / weight_sum for axis in MOOD_AXES}

    totals = {axis: 0.0 for axis in MOOD_AXES}
    weight_sum = 0.0
    for raw_key, percent in breakdown.items():
        cat = alias_map.get(_norm(str(raw_key)))
        if not cat:
            continue
        try:
            w = float(percent)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        cw = cat_weights.get(cat) or {}
        weight_sum += w
        for axis in MOOD_AXES:
            totals[axis] += w * float(cw.get(axis, 0.0))

    if weight_sum <= 0:
        return {axis: 0.0 for axis in MOOD_AXES}
    return {axis: totals[axis] / weight_sum for axis in MOOD_AXES}


def _fetch_axis_scores(review_ids: List[str]) -> List[Dict[str, Any]]:
    if not review_ids:
        return []
    out: List[Dict[str, Any]] = []
    chunk = 100
    for i in range(0, len(review_ids), chunk):
        part = review_ids[i : i + chunk]
        rows = (
            supabase.table("review_axis_scores")
            .select("review_id,axis_key,score,method")
            .in_("review_id", part)
            .execute()
            .data
            or []
        )
        out.extend(rows)
    return out


def _blend_axis_value(
    llm: Optional[float],
    lexicon: Optional[float],
) -> Optional[float]:
    if llm is not None and lexicon is not None:
        w = LEXICON_BLEND_WEIGHT_LLM
        return w * llm + (1.0 - w) * lexicon
    if llm is not None:
        return llm
    if lexicon is not None:
        return lexicon
    return None


def _review_score_maps(
    perfume_id: str,
) -> Tuple[
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, float]],
    Dict[str, float],
    List[str],
]:
    """Return (llm_by_review, lexicon_by_review, helpfulness_weights, review_ids)."""
    reviews = (
        supabase.table("reviews")
        .select("id,vote_yes,vote_no")
        .eq("perfume_id", perfume_id)
        .execute()
        .data
        or []
    )
    review_ids = [r["id"] for r in reviews]
    weights: Dict[str, float] = {
        r["id"]: _helpfulness_weight(r.get("vote_yes"), r.get("vote_no"))
        for r in reviews
    }
    llm_by: Dict[str, Dict[str, float]] = {}
    lex_by: Dict[str, Dict[str, float]] = {}
    for row in _fetch_axis_scores(review_ids):
        rid = row["review_id"]
        key = row["axis_key"]
        score = float(row["score"])
        if row["method"] == "llm":
            llm_by.setdefault(rid, {})[key] = score
        elif row["method"] == "lexicon":
            lex_by.setdefault(rid, {})[key] = score
    return llm_by, lex_by, weights, review_ids


def _opinion_score_maps(
    perfume_id: str,
) -> Tuple[
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, float]],
    Dict[str, float],
]:
    """
    Return (llm_by_opinion_text, lexicon_by_opinion_text, opinion_weights).
    Only character-relevant opinions exist in the table (filtered at score time).
    """
    response = (
        supabase.table("perfume_opinion_scores")
        .select("opinion_text,axis_key,score,method,fragrantica_score")
        .eq("perfume_id", perfume_id)
        .execute()
    )
    llm_by: Dict[str, Dict[str, float]] = {}
    lex_by: Dict[str, Dict[str, float]] = {}
    weights: Dict[str, float] = {}
    for row in response.data or []:
        text = row["opinion_text"]
        key = row["axis_key"]
        score = float(row["score"])
        weights[text] = _opinion_weight(row.get("fragrantica_score"))
        if row["method"] == "llm":
            llm_by.setdefault(text, {})[key] = score
        elif row["method"] == "lexicon":
            lex_by.setdefault(text, {})[key] = score
    return llm_by, lex_by, weights


def _review_posteriors(
    perfume_id: str,
) -> Tuple[Dict[str, float], Dict[str, float], int, Dict[str, float]]:
    """
    Build mood-axis posterior + gates + sample_size + divergence.

    Reviews and character-relevant opinions share one weighted pool.
    sample_size stays review-complete count only (opinions do not inflate n).
    """
    llm_by, lex_by, weights, _ = _review_score_maps(perfume_id)
    o_llm, o_lex, o_weights = _opinion_score_maps(perfume_id)

    if not llm_by and not lex_by and not o_llm and not o_lex:
        return (
            {a: 0.0 for a in MOOD_AXES},
            {g: 0.0 for g in GATE_KEYS},
            0,
            {k: 0.0 for k in LEXICON_BLEND_KEYS},
        )

    def w_review(rid: str) -> float:
        return weights.get(rid, 1.0)

    def w_opinion(text: str) -> float:
        return o_weights.get(text, 1.0)

    complete_ids = [
        rid for rid, scores in llm_by.items() if all(k in scores for k in MOOD_AXES)
    ]
    n = len(complete_ids)

    axes: Dict[str, float] = {}
    for axis in LLM_ONLY_MOOD_AXES:
        pairs: List[Tuple[float, float]] = [
            (llm_by[rid][axis], w_review(rid))
            for rid in complete_ids
            if axis in llm_by.get(rid, {})
        ]
        if not pairs:
            pairs = [
                (scores[axis], w_review(rid))
                for rid, scores in llm_by.items()
                if axis in scores
            ]
        for text, scores in o_llm.items():
            if axis in scores:
                pairs.append((scores[axis], w_opinion(text)))
        axes[axis] = _weighted_mean(pairs)

    # Valence & dominance: same helpfulness / opinion weights as LLM-only axes.
    # Per item: optional LLM+lexicon blend, then weighted mean across the pool.
    dom_pairs: List[Tuple[float, float]] = []
    for rid in set(llm_by) | set(lex_by):
        blended = _blend_axis_value(
            llm_by.get(rid, {}).get("dominance"),
            lex_by.get(rid, {}).get("dominance"),
        )
        if blended is not None:
            dom_pairs.append((blended, w_review(rid)))
    for text in set(o_llm) | set(o_lex):
        blended = _blend_axis_value(
            o_llm.get(text, {}).get("dominance"),
            o_lex.get(text, {}).get("dominance"),
        )
        if blended is not None:
            dom_pairs.append((blended, w_opinion(text)))
    axes["dominance"] = _weighted_mean(dom_pairs)

    gates: Dict[str, float] = {}
    disgust_pairs = [
        (scores["disgust"], w_review(rid))
        for rid, scores in llm_by.items()
        if "disgust" in scores
    ]
    for text, scores in o_llm.items():
        if "disgust" in scores:
            disgust_pairs.append((scores["disgust"], w_opinion(text)))
    gates["disgust"] = _weighted_mean(disgust_pairs)

    val_pairs: List[Tuple[float, float]] = []
    for rid in set(llm_by) | set(lex_by):
        blended = _blend_axis_value(
            llm_by.get(rid, {}).get("valence"),
            lex_by.get(rid, {}).get("valence"),
        )
        if blended is not None:
            val_pairs.append((blended, w_review(rid)))
    for text in set(o_llm) | set(o_lex):
        blended = _blend_axis_value(
            o_llm.get(text, {}).get("valence"),
            o_lex.get(text, {}).get("valence"),
        )
        if blended is not None:
            val_pairs.append((blended, w_opinion(text)))
    gates["valence"] = _weighted_mean(val_pairs)

    divergence: Dict[str, float] = {}
    for key in LEXICON_BLEND_KEYS:
        diff_pairs: List[Tuple[float, float]] = []
        for rid in set(llm_by) | set(lex_by):
            lv = llm_by.get(rid, {}).get(key)
            xv = lex_by.get(rid, {}).get(key)
            if lv is not None and xv is not None:
                diff_pairs.append((abs(lv - xv), w_review(rid)))
        for text in set(o_llm) | set(o_lex):
            lv = o_llm.get(text, {}).get(key)
            xv = o_lex.get(text, {}).get(key)
            if lv is not None and xv is not None:
                diff_pairs.append((abs(lv - xv), w_opinion(text)))
        divergence[key] = _weighted_mean(diff_pairs)

    return axes, gates, n, divergence


def _blend(prior: Dict[str, float], posterior: Dict[str, float], review_count: int) -> Dict[str, float]:
    if review_count <= 0:
        return dict(prior)
    w = min(1.0, review_count / float(REVIEW_BLEND_THRESHOLD))
    return {
        axis: prior.get(axis, 0.0) * (1.0 - w) + posterior.get(axis, 0.0) * w
        for axis in MOOD_AXES
    }


def _step_down(conf: str) -> str:
    if conf == "high":
        return "medium"
    if conf == "medium":
        return "low_prior"
    return conf


def _confidence(
    review_count: int,
    valence: float,
    gated_out: bool,
    divergence: Dict[str, float],
) -> Tuple[str, bool]:
    if review_count <= 0:
        conf = "low_prior"
    elif review_count < REVIEW_BLEND_THRESHOLD / 2:
        conf = "medium"
    else:
        conf = "high"

    if valence < VALENCE_FLOOR:
        conf = _step_down(conf)
    if gated_out and conf == "high":
        conf = "medium"

    downgraded_by_divergence = False
    if any(
        divergence.get(k, 0.0) > LEXICON_DIVERGENCE_THRESHOLD for k in LEXICON_BLEND_KEYS
    ):
        before = conf
        conf = _step_down(conf)
        downgraded_by_divergence = conf != before

    return conf, downgraded_by_divergence


def _mood_scores_from_axes(
    axes: Dict[str, float],
    label_weights: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for mood_key, weights in label_weights.items():
        total_w = sum(weights.values()) or 1.0
        score = sum(axes.get(axis, 0.0) * (w / total_w) for axis, w in weights.items())
        out[mood_key] = round(score, 2)
    return out


def compute_moods(perfume_id: Optional[str] = None) -> Dict[str, Any]:
    alias_map = _load_alias_map()
    cat_weights = _load_category_weights()
    label_weights = _load_mood_label_weights()

    query = supabase.table("perfumes").select(
        "id,name,accord_breakdown,notes_top,notes_middle,notes_base"
    )
    if perfume_id:
        query = query.eq("id", perfume_id)
    perfumes = query.execute().data or []

    updated = 0
    gated_out_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for perfume in perfumes:
        prior = _note_prior(perfume, alias_map, cat_weights)
        posterior, gates, review_count, divergence = _review_posteriors(perfume["id"])
        blended = _blend(prior, posterior, review_count)

        disgust = gates.get("disgust", 0.0)
        has_review_signal = review_count > 0 or any(
            divergence.get(k, 0.0) > 0 for k in LEXICON_BLEND_KEYS
        ) or gates.get("valence", 0) > 0 or gates.get("disgust", 0) > 0
        valence = gates.get("valence", 50.0 if not has_review_signal else 0.0)
        gated_out = bool(review_count > 0 and disgust >= DISGUST_GATE)
        if gated_out:
            gated_out_count += 1

        valence_for_conf = valence if has_review_signal else 100.0
        conf, _ = _confidence(review_count, valence_for_conf, gated_out, divergence)
        moods = _mood_scores_from_axes(blended, label_weights)

        rows = [
            {
                "perfume_id": perfume["id"],
                "mood_key": mood_key,
                "score": score,
                "sample_size": review_count,
                "confidence": conf,
                "gated_out": gated_out,
                "computed_at": now,
            }
            for mood_key, score in moods.items()
        ]
        if not rows:
            continue
        supabase.table("perfume_mood_scores").upsert(
            rows,
            on_conflict="perfume_id,mood_key",
        ).execute()
        updated += 1

    return {
        "status": "success",
        "message": f"Updated mood scores for {updated} perfume(s)",
        "updated_perfumes": updated,
        "gated_out_count": gated_out_count,
    }


def lexicon_check(perfume_id: str) -> Dict[str, Any]:
    """
    Transparency into valence/dominance sources vs the posterior used in compute.

    Single top-level `divergence` from `_review_posteriors` (helpfulness-weighted
    reviews + opinion weights). Per-axis objects expose llm/lexicon means and the
    same blended value that feeds mood compute — no nested duplicate divergence.
    """
    llm_by, lex_by, weights, _ = _review_score_maps(perfume_id)
    o_llm, o_lex, o_weights = _opinion_score_maps(perfume_id)
    axes, gates, n, divergence = _review_posteriors(perfume_id)

    def w_review(rid: str) -> float:
        return weights.get(rid, 1.0)

    def w_opinion(text: str) -> float:
        return o_weights.get(text, 1.0)

    def _wmean_opt(pairs: List[Tuple[float, float]]) -> Optional[float]:
        if not pairs:
            return None
        return round(_weighted_mean(pairs), 2)

    result: Dict[str, Any] = {"perfume_id": perfume_id}

    for key in LEXICON_BLEND_KEYS:
        llm_pairs: List[Tuple[float, float]] = []
        lex_pairs: List[Tuple[float, float]] = []
        for rid in set(llm_by) | set(lex_by):
            ww = w_review(rid)
            lv = llm_by.get(rid, {}).get(key)
            xv = lex_by.get(rid, {}).get(key)
            if lv is not None:
                llm_pairs.append((lv, ww))
            if xv is not None:
                lex_pairs.append((xv, ww))
        for text in set(o_llm) | set(o_lex):
            ww = w_opinion(text)
            lv = o_llm.get(text, {}).get(key)
            xv = o_lex.get(text, {}).get(key)
            if lv is not None:
                llm_pairs.append((lv, ww))
            if xv is not None:
                lex_pairs.append((xv, ww))

        if key == "valence":
            blended_val = gates.get("valence")
        else:
            blended_val = axes.get("dominance")

        result[key] = {
            "llm_mean": _wmean_opt(llm_pairs),
            "lexicon_mean": _wmean_opt(lex_pairs),
            "blended": round(float(blended_val), 2) if blended_val is not None else None,
        }

    disgust = gates.get("disgust", 0.0)
    valence = gates.get("valence", 0.0)
    gated_out = bool(n > 0 and disgust >= DISGUST_GATE)
    _, downgraded = _confidence(n, valence if n > 0 else 100.0, gated_out, divergence)

    both = lex_only = llm_only = 0
    for rid in set(llm_by) | set(lex_by):
        has_llm = any(k in llm_by.get(rid, {}) for k in LEXICON_BLEND_KEYS)
        has_lex = any(k in lex_by.get(rid, {}) for k in LEXICON_BLEND_KEYS)
        if has_llm and has_lex:
            both += 1
        elif has_llm:
            llm_only += 1
        elif has_lex:
            lex_only += 1

    result["reviews_both"] = both
    result["reviews_llm_only"] = llm_only
    result["reviews_lexicon_only"] = lex_only
    result["sample_size_llm_complete"] = n
    # Single canonical divergence — same weighted pool as mood_compute posteriors
    result["divergence"] = {k: round(v, 2) for k, v in divergence.items()}
    result["confidence_downgraded_by_divergence"] = downgraded
    result["blend_weight_llm"] = LEXICON_BLEND_WEIGHT_LLM
    result["helpfulness_weight_cap"] = REVIEW_HELPFULNESS_WEIGHT_CAP
    result["pros_cons_weight_cap"] = PROS_CONS_WEIGHT_CAP
    return result
