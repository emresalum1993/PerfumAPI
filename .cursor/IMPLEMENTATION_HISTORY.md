# IMPLEMENTATION_HISTORY.md

Kronolojik kısa özetler. Agent her anlamlı değişiklikten sonra buraya 2–5 satır ekler.

---

## 2026-08-26 — Fix lexicon-check divergence + V/D weighting clarity

- `lexicon_check`: remove nested per-axis `divergence`; one top-level `divergence` from `_review_posteriors` (weighted reviews+opinions).
- `valence`/`dominance`.blended now equal posterior gates/axes (same weighting path as disgust/5 axes).

---

## 2026-08-26 — Pros/cons opinion scoring

- Migration `007` `perfume_opinion_scores`; `pipeline/opinion_scorer.py` + `score_opinion_text` (character_relevant gate).
- API: `POST /pipeline/opinions/score?perfume_id=&rescore=` (not in run-all).
- `mood_compute`: opinions share weighted posterior pool with reviews; `sample_size` still review-only.
- Env: `LEXICON_MIN_MATCHED_WORDS_OPINIONS=3`, `PROS_CONS_WEIGHT_CAP=8`.

---

## 2026-08-26 — Helpfulness-weighted posteriors

- `mood_compute`: posterior / gates / divergence use `weight = min(1+ln(1+max(0,yes-no)), CAP)`; never sentiment.
- Env: `REVIEW_HELPFULNESS_WEIGHT_CAP` (default 5). `lexicon-check` means weighted the same way.
- Docs: MOOD_SCORING §3b; karma_score noted as future. Recompute-only (no LLM) for BR540 verify.

---

## 2026-08-26 — NRC-VAD lexicon integration

- `pipeline/lexicon_scorer.py` + `scripts/load_lexicon.py`: unigrams → `emotion_lexicon` (raw −1..+1; rescale 0–100 at score).
- Review score writes `method=lexicon` valence/dominance (MIN matches); LLM fail still tries lexicon; `rescore` clears both.
- `mood_compute`: blend V/D (`LEXICON_BLEND_WEIGHT_LLM`); divergence confidence step-down.
- API: `POST /pipeline/reviews/score-lexicon`, `GET /perfumes/{id}/lexicon-check`.
- Loaded 44728 unigrams; Baccarat backfill 20/20; valence divergence stepped confidence high→medium.
- Docs: MOOD_SCORING / AGENTS / SQL_HISTORY. Migration `006` optional (raw storage fits numeric(4,2)).

---

## 2026-08-26 — Mood scoring technical doc

- Added `.cursor/MOOD_SCORING.md`: axes/gates/labels, LLM review scores, note prior, blend formula, gates/confidence, APIs, Baccarat example.
- Linked from `AGENTS.md`.

---

## 2026-08-26 — Pipeline rescore=true

- `score_reviews(..., rescore=)` + `clear_llm_scores_for_perfume`: wipe `method=llm` scores for one perfume, then re-LLM.
- API: `POST /pipeline/reviews/score?rescore=` and `POST /pipeline/run-all?rescore=` — **requires `perfume_id`** (400 if missing).
- Docs: `AGENTS.md`.

---

## 2026-08-26 — Public perfume summary

- `GET /perfumes/{id}/summary` (no auth): identity, rating, top 5 accords, mood snapshot (`top_mood` + compact `moods`), sample_size/confidence/gated_out, reviews_total.
- Docs: `AGENTS.md` endpoint table.

---

## 2026-08-26 — Mood pipeline implemented

- `pipeline/`: `notes.check_unmapped`, `review_scorer.score_reviews` (OmniRoute OpenAI-compatible), `mood_compute.compute_moods`.
- API: `POST /pipeline/notes/check-unmapped`, `/pipeline/reviews/score`, `/pipeline/moods/compute`, `/pipeline/run-all`; `GET /perfumes/{id}/moods`.
- Env: `LLM_BASE_URL` (default `http://localhost:20128/v1`), `LLM_API_KEY`, `LLM_MODEL`, `PIPELINE_*` — `env.example`.
- Dep: `openai` in `requirements.txt`.
- Smoke: Baccarat unmapped=`metallic`; OmniRoute scored 1 review; moods upserted (top: powerful/elegant/romantic).
- Docs: `AGENTS.md` history kuralı; üst katman SQL_SCHEMAS ile DB’de (006–009 atlandı).

---

## 2026-08-26 — Docs layout + plan

- Dokümanlar `.cursor/` altında.
- Pipeline plan: ayrı endpoint’ler + run-all; OmniRoute LLM.
