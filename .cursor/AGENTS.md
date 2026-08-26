# AGENTS.md — Perfume API / Akord

Bu dosya, projede çalışan agent’lar için kalıcı bağlamdır.

| Belge | Ne için |
|-------|---------|
| [`SQL_SCHEMAS.MD`](./SQL_SCHEMAS.MD) | Tam hedef şema **v0.3** — **tek doğruluk kaynağı** |
| [`SQL_HISTORY.MD`](./SQL_HISTORY.MD) | Migration geçmişi |
| [`IMPLEMENTATION_HISTORY.md`](./IMPLEMENTATION_HISTORY.md) | Yapılan işlerin kısa kronolojisi — **agent nerede kaldığını buradan okur** |
| [`AUTOMATION_IMPLEMENTATION.MD`](./AUTOMATION_IMPLEMENTATION.MD) | Mood pipeline endpoint tasarımı |
| [`MOOD_SCORING.md`](./MOOD_SCORING.md) | Mood scoring teknik özeti (prior → LLM → blend → labels) |
| [`../migrations/`](../migrations/) | Incremental SQL (repo kökü) |

Şema/migration değişince: `SQL_SCHEMAS.MD` + gerekirse migration + `SQL_HISTORY.MD` + **`IMPLEMENTATION_HISTORY.md` özeti**.

---

## Zorunlu: IMPLEMENTATION_HISTORY

Her anlamlı değişiklikten sonra [IMPLEMENTATION_HISTORY.md](./IMPLEMENTATION_HISTORY.md) dosyasına **2–5 satır** ekle:

- Ne yapıldı
- Hangi dosyalar / endpoint’ler
- Bilinen eksik / sonraki adım

Aksi halde sonraki agent bağlam kaybeder.

---

## Proje özeti

- **Repo:** Fragrantica scrape + FastAPI + Supabase + mood pipeline.
- **API:** `api/main.py` — public okuma, auth’lu scrape + pipeline.
- **Scraper:** `scraper/scrape.py` + `utils/fragrantica_crypto.py`.
- **Pipeline:** `pipeline/` — unmapped notes → OmniRoute LLM + NRC-VAD lexicon → `perfume_mood_scores`.
- **LLM:** OmniRoute OpenAI-compatible (`LLM_BASE_URL=http://localhost:20128/v1`).
- **Lexicon:** NRC-VAD unigrams → `emotion_lexicon`; blends **valence + dominance** only.

---

## Migration’lar (özet)

Detay → [`SQL_HISTORY.MD`](./SQL_HISTORY.MD).

| # | Dosya | Özet |
|---|--------|------|
| 001–005 | `migrations/00x_*.sql` | perfumes + reviews + trgm |
| 006 | `006_widen_emotion_lexicon_scores.sql` | emotion_lexicon VAD → numeric(5,2) |
| Üst katman 1–4 | `SQL_SCHEMAS.MD` ile DB’ye uygulandı | mood/gates/notes/score + emotion_lexicon |

---

## Veri katmanı (0)

### `perfumes` / `reviews`

Scrape master + review satırları. Detay alanlar için `SQL_SCHEMAS.MD` §0.

---

## Üst katman (v0.3)

| Katman | Tablolar | Not |
|--------|----------|-----|
| 1 | `mood_axes` (6), `mood_labels`, `mood_label_weights` | Gate yok |
| 1b | `quality_gates` | disgust, valence |
| 2 | `note_categories` (30), `note_aliases`, `note_category_axis_weights` | |
| 3 | `emotion_lexicon` | NRC-VAD unigrams (0–100) |
| 4 | `perfume_mood_scores`, `review_axis_scores`, `perfume_opinion_scores` | `method` ∈ llm, lexicon |

Alias: scrape `warm spicy` → alias → kategori `warm_spicy`.

---

## Pipeline API (auth)

| Endpoint | Ne yapar |
|----------|----------|
| `POST /pipeline/notes/check-unmapped` | Unmapped accord/note listesi |
| `POST /pipeline/reviews/score?limit=&perfume_id=&rescore=` | LLM (8) + lexicon (valence/dominance); `rescore` siler llm+lexicon |
| `POST /pipeline/reviews/score-lexicon?perfume_id=&limit=` | Lexicon backfill only (no LLM) |
| `POST /pipeline/opinions/score?perfume_id=&rescore=` | Pros/cons LLM+lexicon (character filter); not in run-all |
| `POST /pipeline/moods/compute?perfume_id=` | Prior+posterior+gates → `perfume_mood_scores` |
| `POST /pipeline/run-all?force=&rescore=&score_limit=` | Orkestratör (unmapped → 409 unless force; rescore için perfume_id) |
| `GET /perfumes/{id}/moods` | Public mood skorları |
| `GET /perfumes/{id}/summary` | Public kompakt kart |
| `GET /perfumes/{id}/lexicon-check` | LLM vs lexicon vs blended V/D |

Env: `LLM_*`, `PIPELINE_*`, `LEXICON_*`, `REVIEW_HELPFULNESS_WEIGHT_CAP`, `PROS_CONS_WEIGHT_CAP`, `LEXICON_MIN_MATCHED_WORDS_OPINIONS` — `env.example`.

---

## Agent kuralları

1. Şema → `SQL_SCHEMAS.MD` / migration / `SQL_HISTORY.MD`.
2. **Her iş bitiminde `IMPLEMENTATION_HISTORY.md` güncelle.**
3. Scrape: crypto passphrase dönebilir; Cloudflare 403 mümkün.
4. Auth: scrape + pipeline Bearer JWT.
5. Gate’i mood weight’e ekleme.
6. Similar JSONB kalır.
7. LLM varsayılanı OmniRoute local; cloud OpenAI için sadece `LLM_BASE_URL` değiştir.

---

## Hızlı API referansı

| Endpoint | Auth |
|----------|------|
| `GET /perfumes`, `GET /perfumes/{id}`, `GET /perfumes/{id}/reviews`, `GET /perfumes/{id}/moods`, `GET /perfumes/{id}/summary`, `GET /perfumes/{id}/lexicon-check` | Hayır |
| `POST /scrape/*`, `POST /scrape/reviews/{id}` | Evet |
| `POST /pipeline/*` | Evet |
