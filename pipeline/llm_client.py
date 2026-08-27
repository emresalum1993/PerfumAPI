"""OpenAI-compatible LLM client (OmniRoute / OpenAI / any /v1 gateway)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from openai import OpenAI

from pipeline.constants import (
    AI_OVERVIEW_TEMPERATURE,
    ALL_SCORE_KEYS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)


class LLMUnavailableError(RuntimeError):
    pass


def is_llm_configured() -> bool:
    return bool(LLM_BASE_URL)


def get_client() -> OpenAI:
    # OmniRoute accepts a dashboard key; empty key may still work on some local setups.
    return OpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY or "omniroute-local",
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty LLM response")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("No JSON object in LLM response")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM JSON is not an object")
    return data


def _normalize_scores(raw: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key in ALL_SCORE_KEYS:
        if key not in raw:
            raise ValueError(f"Missing score key: {key}")
        try:
            val = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid score for {key}: {raw[key]}") from exc
        out[key] = max(0.0, min(100.0, val))
    return out


def _chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    client = get_client()
    last_err: Optional[Exception] = None
    for _ in range(2):
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            return _extract_json_object(content)
        except Exception as exc:
            last_err = exc
            try:
                resp = client.chat.completions.create(
                    model=LLM_MODEL,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                return _extract_json_object(content)
            except Exception as exc2:
                last_err = exc2
                continue
    raise LLMUnavailableError(f"LLM scoring failed: {last_err}")


def _rubric_lines(
    axis_labels: Dict[str, str],
    gate_labels: Dict[str, str],
) -> str:
    lines = []
    for key in ALL_SCORE_KEYS[:6]:
        label = axis_labels.get(key, key)
        lines.append(f"- {key}: {label} (mood axis; higher = more of this feeling)")
    for key in ALL_SCORE_KEYS[6:]:
        label = gate_labels.get(key, key)
        lines.append(f"- {key}: {label} (quality gate; not a user-facing mood)")
    return "\n".join(lines)


def score_review_text(
    content_text: str,
    *,
    sentiment: Optional[str] = None,
    axis_labels: Optional[Dict[str, str]] = None,
    gate_labels: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """
    One LLM call → 8 scores (6 mood axes + 2 gates), each 0–100.
    """
    if not is_llm_configured():
        raise LLMUnavailableError("LLM_BASE_URL is not set")

    axis_labels = axis_labels or {}
    gate_labels = gate_labels or {}

    system = (
        "You score perfume review text for fragrance emotion research.\n"
        "Return ONLY a JSON object with exactly these keys as numbers 0-100:\n"
        f"{', '.join(ALL_SCORE_KEYS)}.\n"
        "Rubric:\n" + _rubric_lines(axis_labels, gate_labels) + "\n"
        "disgust: how much irritation/disgust the review expresses (high = bad quality signal).\n"
        "valence: overall positivity of the review (high = positive).\n"
        "No markdown, no commentary."
    )
    user = f"Sentiment filter (if any): {sentiment or 'unknown'}\n\nReview:\n{content_text[:4000]}"
    return _normalize_scores(_chat_json(system, user))


def score_opinion_text(
    opinion_text: str,
    *,
    opinion_type: Optional[str] = None,
    axis_labels: Optional[Dict[str, str]] = None,
    gate_labels: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    One LLM call → 8 scores (0–100) + character_relevant bool.
    character_relevant=false means the opinion is not about scent character
    (price, longevity, season, gender, dupes, etc.) and must be discarded.
    """
    if not is_llm_configured():
        raise LLMUnavailableError("LLM_BASE_URL is not set")

    axis_labels = axis_labels or {}
    gate_labels = gate_labels or {}

    system = (
        "You score short Fragrantica pro/con opinion lines for fragrance emotion research.\n"
        "Return ONLY a JSON object with these keys:\n"
        f"{', '.join(ALL_SCORE_KEYS)} (numbers 0-100), and character_relevant (boolean).\n"
        "Rubric:\n" + _rubric_lines(axis_labels, gate_labels) + "\n"
        "disgust: irritation/disgust about the scent itself (high = bad quality signal).\n"
        "valence: positivity about the scent experience (high = positive).\n"
        "character_relevant: true ONLY if the opinion describes how the perfume smells "
        "or feels (scent character, mood, olfactory complaints like polarizing, "
        "nauseating, sweet, metallic). "
        "character_relevant: false for price/value, longevity/sillage/performance, "
        "season or skin chemistry fit, gender suitability, overexposure/popularity, "
        "dupes/clones, packaging, or anything not about scent character.\n"
        "If character_relevant is false, still include all score keys (use 0) — "
        "the caller will discard the row.\n"
        "No markdown, no commentary."
    )
    user = (
        f"Opinion type: {opinion_type or 'unknown'}\n\n"
        f"Opinion:\n{(opinion_text or '')[:2000]}"
    )
    raw = _chat_json(system, user)
    if "character_relevant" not in raw:
        raise ValueError("Missing character_relevant in LLM response")
    relevant = raw["character_relevant"]
    if isinstance(relevant, str):
        relevant = relevant.strip().lower() in ("true", "1", "yes")
    else:
        relevant = bool(relevant)
    scores = _normalize_scores(raw)
    scores["character_relevant"] = relevant
    return scores


def generate_ai_overview(
    *,
    perfume_name: str,
    brand: Optional[str],
    opinions_block: str,
    reviews_block: str,
    polarizing: bool = False,
) -> Dict[str, Any]:
    """
    One LLM call → original summary + pros/cons chips + fuller pros_list/cons_list.
    Caller validates lengths and verbatim copying.
    """
    if not is_llm_configured():
        raise LLMUnavailableError("LLM_BASE_URL is not set")

    polarizing_line = (
        "Reviews/opinions show a polarizing or divisive scent reaction — "
        "the summary MUST reflect that people are split, not a falsely uniform picture.\n"
        if polarizing
        else ""
    )

    system = (
        "Write an original summary and pros/cons for this perfume, based only "
        "on the reviews and opinions provided below. Do not copy phrases "
        "verbatim from the source material — paraphrase and synthesize in your "
        "own words. Do not state anything not supported by the provided text.\n"
        f"{polarizing_line}"
        "Return JSON only with these keys:\n"
        "{\n"
        '  "summary": "2-4 sentences, neutral tone, covering both what people '
        'love and what divides opinion if the reviews are mixed",\n'
        '  "pros": ["3-5 short original phrases — quick highlights"],\n'
        '  "cons": ["3-5 short original phrases — quick highlights"],\n'
        '  "pros_list": ["6-10 items, each ONE short original sentence — a '
        "fuller list than 'pros', for a detail view\"],\n"
        '  "cons_list": ["6-10 items, each ONE short original sentence — a '
        "fuller list than 'cons', for a detail view\"]\n"
        "}\n"
        "pros_list/cons_list must expand pros/cons: every chip theme should "
        "appear (possibly reworded) in the matching fuller list; add more "
        "supported points with slightly fuller sentences — not unrelated extras.\n"
        "No markdown, no commentary."
    )
    user = (
        f"Perfume: {perfume_name}\n"
        f"Brand: {brand or 'unknown'}\n\n"
        f"Fragrantica opinions (pros/cons text only):\n{opinions_block}\n\n"
        f"Selected reviews (stratified by sentiment):\n{reviews_block}"
    )
    return _chat_json(system, user, temperature=AI_OVERVIEW_TEMPERATURE)
