# AI-generated perfume overview

Merged plan (chips + detail lists). Separate from mood scoring.

## Rules

- Do **not** modify `mood_compute.py`, `review_scorer.py`, or `opinion_scorer.py`.
- Reuse `llm_client.py` / OmniRoute (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`).
- Original synthesis only — no near-copies of Fragrantica or review phrasing.
- Generation only on authenticated POST; public GET never calls the LLM.

## Table

`migrations/008_create_perfume_ai_overviews.sql` — one row per perfume (upsert on regenerate):

- `summary`, `pros`, `cons`, `pros_list`, `cons_list`
- `model`, `input_review_count`, `input_opinion_count`, `generated_at`

## Routes

| Method | Path | Auth |
|--------|------|------|
| `POST` | `/pipeline/ai-overview/generate?perfume_id=&force=` | Yes — skip LLM if row exists unless `force=true` |
| `GET` | `/perfumes/{id}/ai-overview` | No — `{"generated": false}` if missing |

Not in `run-all`.

## Input selection

- All Fragrantica `pros` / `cons` opinion texts.
- Reviews: stratify by `sentiment` (positive / negative); within each side rank by  
  `min(1 + ln(1 + max(0, vote_yes - vote_no)), REVIEW_HELPFULNESS_WEIGHT_CAP)`;  
  take top `AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT` (default 20) per side.
- Truncate each review to `AI_OVERVIEW_REVIEW_CHAR_LIMIT` (default 600).
- If `perfume_mood_scores.gated_out` is true, prompt asks for a divisive summary.
- 0 reviews and empty pros/cons → `insufficient_data` (422); no hallucinated row.

## Output contract

```json
{
  "summary": "2-4 sentences…",
  "pros": ["3-5 short chips"],
  "cons": ["3-5 short chips"],
  "pros_list": ["6-10 one-sentence items"],
  "cons_list": ["6-10 one-sentence items"]
}
```

`pros_list` / `cons_list` expand the chips (every chip theme represented + more).

## Validation (retry up to `AI_OVERVIEW_MAX_ATTEMPTS`)

1. Length ranges above  
2. Verbatim: case-insensitive ≥6-word substring overlap vs sources (all four lists + summary)  
3. Soft chip→list coverage (one extra retry, then accept if hard checks pass)

## Config

| Env | Default |
|-----|---------|
| `AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT` | `20` |
| `AI_OVERVIEW_REVIEW_CHAR_LIMIT` | `600` |
| `AI_OVERVIEW_MAX_ATTEMPTS` | `3` |
| `AI_OVERVIEW_TEMPERATURE` | `0.3` |

## Code

- `pipeline/ai_overview.py`
- `pipeline/llm_client.generate_ai_overview`
- Operator flow: [`PROCESS.md`](../PROCESS.md)
