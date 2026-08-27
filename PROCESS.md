# Perfume pipeline process

Operator guide: scrape a perfume → scrape reviews → score moods → generate AI overview.

API default: `http://localhost:9000`  
Auth: `Authorization: Bearer <supabase_access_token>` on all `/scrape/*` and `/pipeline/*` routes.

```bash
# Get a JWT (uses TEST_USER / TEST_PASSWORD from .env)
TOKEN=$(curl -sS -X POST "${SUPABASE_URL}/auth/v1/token?grant_type=password" \
  -H "apikey: ${SUPABASE_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${TEST_USER}\",\"password\":\"${TEST_PASSWORD}\"}" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
```

Interactive docs: http://localhost:9000/docs

---

## Flow (one perfume)

```
1. Scrape perfume page     → perfumes row (notes, accords, pros/cons, fragrantica_id, …)
2. Scrape reviews          → reviews rows (positive + negative)
3. Score pipeline          → review_axis_scores (+ optional opinion scores) → perfume_mood_scores
4. AI overview             → perfume_ai_overviews (summary + pros/cons chips + detail lists)
```

Mood scoring and AI overview are **separate**. Overview does not write mood tables; mood compute does not write overviews. `run-all` does **not** include AI overview or opinion scoring.

---

## 1. Scrape perfume details

Pick one:

**By Fragrantica URL (recommended for a single perfume)**

```bash
curl -sS -X POST "http://127.0.0.1:9000/scrape/url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"perfume_url":"https://www.fragrantica.com/perfume/Maison-Francis-Kurkdjian/Baccarat-Rouge-540-33519.html"}'
```

**By brand / batch / popular list:** `POST /scrape/brand`, `POST /scrape/brands`, `POST /scrape`

Confirm the row:

```bash
curl -sS "http://127.0.0.1:9000/perfumes/search/Baccarat"
```

You need `id` (our UUID), `fragrantica_id`, and `perfume_url` for review scrape.

---

## 2. Scrape reviews

Reviews are scraped **separately** for positive and negative sentiment (do both).

```bash
PERFUME_ID=<uuid>

curl -sS -X POST "http://127.0.0.1:9000/scrape/reviews/${PERFUME_ID}?pages=5&sentiment=positive" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "http://127.0.0.1:9000/scrape/reviews/${PERFUME_ID}?pages=5&sentiment=negative" \
  -H "Authorization: Bearer $TOKEN"
```

Check: `GET /perfumes/{id}/reviews`

---

## 3. Score it (mood pipeline)

Optional but recommended order:

| Step | Endpoint | Notes |
|------|----------|--------|
| Unmapped notes | `POST /pipeline/notes/check-unmapped?perfume_id=` | Fix `note_aliases` or use `force=true` on run-all |
| Score reviews | `POST /pipeline/reviews/score?perfume_id=&limit=` | LLM (8 axes) + lexicon V/D; `rescore=true` wipes then redoes |
| Score opinions | `POST /pipeline/opinions/score?perfume_id=` | Character-relevant Fragrantica pros/cons → mood blend; **not** in run-all |
| Compute moods | `POST /pipeline/moods/compute?perfume_id=` | Pure math upsert to `perfume_mood_scores` |

**One-shot orchestrator** (notes → review score → moods; no opinions, no AI overview):

```bash
curl -sS -X POST "http://127.0.0.1:9000/pipeline/run-all?perfume_id=${PERFUME_ID}&force=true" \
  -H "Authorization: Bearer $TOKEN"
```

Public reads:

- `GET /perfumes/{id}/moods`
- `GET /perfumes/{id}/summary`
- `GET /perfumes/{id}/lexicon-check`

Requires: `LLM_BASE_URL` (OmniRoute), lexicon loaded, mood schema / migrations applied. See `.cursor/MOOD_SCORING.md`.

---

## 4. Create AI overview

**Prerequisite:** migration `008_create_perfume_ai_overviews.sql` applied in Supabase.

Uses reviews + Fragrantica `pros`/`cons` text (stratified helpfulness sample). Writes **original** paraphrase — not a copy of Fragrantica chips. Optional polarizing hint from existing `perfume_mood_scores.gated_out`.

```bash
# Generate (skips LLM if row already exists)
curl -sS -X POST "http://127.0.0.1:9000/pipeline/ai-overview/generate?perfume_id=${PERFUME_ID}" \
  -H "Authorization: Bearer $TOKEN"

# Force regenerate
curl -sS -X POST "http://127.0.0.1:9000/pipeline/ai-overview/generate?perfume_id=${PERFUME_ID}&force=true" \
  -H "Authorization: Bearer $TOKEN"

# Public read (never calls LLM)
curl -sS "http://127.0.0.1:9000/perfumes/${PERFUME_ID}/ai-overview"
```

Response fields: `summary`, `pros` / `cons` (3–5 short chips), `pros_list` / `cons_list` (6–10 one-sentence items).

Empty reviews **and** empty pros/cons → `422 insufficient_data` (no hallucinated overview).

Config: `AI_OVERVIEW_MAX_REVIEWS_PER_SENTIMENT`, `AI_OVERVIEW_REVIEW_CHAR_LIMIT` — `env.example`. Design: `.cursor/CURSOR_PROMPT_ai_overview.md`.

---

## Auth vs public

| Auth required | Public |
|---------------|--------|
| `/scrape/*`, `/pipeline/*` | `/perfumes`, `/moods`, `/summary`, `/ai-overview`, `/lexicon-check`, `/reviews` |

Never generate LLM content from a GET.

---

## Migrations to apply (SQL Editor)

Incremental files under `migrations/`. Mood upper layer may already be live via `SQL_SCHEMAS.MD`. For AI overview:

```text
migrations/008_create_perfume_ai_overviews.sql
```

History: `.cursor/SQL_HISTORY.MD` · Agent notes: `.cursor/AGENTS.md` · Chronology: `.cursor/IMPLEMENTATION_HISTORY.md`
