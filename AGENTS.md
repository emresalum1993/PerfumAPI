# AGENTS.md — Perfume API / Akord

Bu dosya, projede çalışan agent’lar için kalıcı bağlamdır.

| Belge | Ne için |
|-------|---------|
| [`SQL_SCHEMAS.MD`](./SQL_SCHEMAS.MD) | Tam hedef şema **v0.3** (katman 0–6 + 1b) — **tek doğruluk kaynağı** |
| [`SQL_HISTORY.MD`](./SQL_HISTORY.MD) | Her `migrations/00N_*.sql` dosyasının ne/neden/bağımlılık geçmişi |
| [`migrations/`](./migrations/) | Production’a uygulanan incremental SQL |

Şema veya migration değişince: `SQL_SCHEMAS.MD` + yeni migration + **`SQL_HISTORY.MD` güncelle** + gerekirse bu dosya.

---

## Proje özeti

- **Repo:** Fragrantica scrape + FastAPI + Supabase (Postgres).
- **Amaç:** Parfüm + review toplamak; mood/öneri katmanı (`SQL_SCHEMAS.MD` 1–6) bunun üzerine.
- **API:** `api/main.py` — public okuma, auth’lu scrape.
- **Scraper:** `scraper/scrape.py` + CryptoJS decrypt `utils/fragrantica_crypto.py`.

---

## Migration’lar (özet)

Detay → [`SQL_HISTORY.MD`](./SQL_HISTORY.MD).

| # | Dosya | Özet |
|---|--------|------|
| 001 | `001_create_perfumes_table.sql` | `perfumes` çekirdek tablo |
| 002 | `002_add_rating_breakdown_and_when_to_wear.sql` | Sentiment + when-to-wear JSONB |
| 003 | `003_add_extended_perfume_fields.sql` | fragrantica_id, accords, breakdown’lar, pros/cons, similar, … |
| 004 | `004_create_reviews_table.sql` | `reviews` tablosu |
| 005 | `005_add_reviews_content_text_trgm_index.sql` | `pg_trgm` + `content_text` GIN index |

Uygulama sırası: **001 → 005**. Üst katman (1–6) henüz migration değil.

---

## Veri katmanı (0) — canlı scrape tabloları

`SQL_SCHEMAS.MD` bölüm **0)** ile uyumlu. Tablo zaten varsa CREATE’i atla; kolon için migration kullan.

### `perfumes`

| | |
|---|---|
| **Ne** | Fragrantica’dan scrape edilen parfüm master kaydı |
| **Ne için** | Liste/detay API; mood skorları ve affiliate linklerinin FK hedefi |
| **Kimlikler** | `id` UUID, `perfume_url` UNIQUE, `fragrantica_id` partial UNIQUE |
| **Kaynak** | `POST /scrape`, `/scrape/brand`, `/scrape/brands`, `/scrape/url` |

Alan grupları:

- **Meta:** `name`, `brand`, `release_year`, `gender`, `fragrance_family`, `perfumer`, `description`, `image_url`, `image_url_og`
- **Notalar / akortlar:** `notes_top|middle|base`, `main_accords` (isim listesi), `accord_breakdown` (`{"woody":100,"amber":98,…}` — Search by accords URL)
- **Oy kırılımları (JSONB):** `rating_breakdown`, `when_to_wear`, `longevity_breakdown`, `sillage_breakdown`, `price_value`, `gender_votes`, `ownership`
- **Özet skorlar:** `rating`, `votes`, `longevity`, `sillage` (avg 0–10; kolon tipi TEXT)
- **Listeler (JSONB):** `pros`, `cons`, `similar_perfumes` — **ayrı similar tablosu yok**

### `reviews`

| | |
|---|---|
| **Ne** | `reviews4perfume_v2` AJAX → decrypt → satır |
| **Ne için** | Review API; ileride `review_axis_scores` girdisi |
| **İlişki** | `perfume_id → perfumes.id` ON DELETE CASCADE |
| **Dedup** | `fragrantica_review_id` UNIQUE |
| **Kaynak** | `POST /scrape/reviews/{perfume_id}?pages=&sentiment=` |
| **Arama** | `005` sonrası `idx_reviews_text_trgm` (`content_text` GIN trgm) |

Alanlar: `sentiment` (`positive`\|`negative`), `content_html` / `content_text`, `vote_yes`/`vote_no`, `karma_score`, `review_date`, `perfume_votes`, `username`, `user_id`, `member_url`, `avatar_url`.

---

## Üst katman tabloları (`SQL_SCHEMAS.MD` v0.3 — 1–6)

Scrape API’sinin parçası değil; planlanmış ürün şeması. Detay + seed → şema dosyası.

| Katman | Tablolar | Zorunlu? | Ne için |
|--------|----------|----------|---------|
| **1 Mod reçetesi** | `mood_axes` (sadece 6), `mood_labels`, `mood_label_weights` | Evet | Moda karışan eksenler + UI mood etiketleri. `is_gate` yok |
| **1b Kalite filtreleri** | `quality_gates` | Evet | `disgust` / `valence` — moda FK **vermez**; pipeline `gated_out` / confidence için |
| **2 Nota taksonomisi** | `note_categories` (**30** Fragrantica wheel), `note_aliases`, `note_category_axis_weights` | Evet | Ham scrape metni → kategori → mood ekseni (weights sadece `mood_axes`’e FK) |
| **3 Sözlük** | `emotion_lexicon` | Opsiyonel | Lexicon skorlama; sadece LLM ise gerekmez |
| **4 Skor** | `perfume_mood_scores`, `review_axis_scores` | Skor zorunlu; axis opsiyonel | `review_axis_scores.axis_key` → mood **veya** gate (FK yok) |
| **5 Ticari** | `retailers`, `purchase_links`, `link_clicks` | Satın alma için | Affiliate + tıklama |
| **6 Uygulama** | `user_selections`, `user_mood_selections` | Öneri için | Kullanıcı wheel/mood seçimleri |

v0.3 kritik kurallar:

- Gate’ler `mood_axes` içinde **değil** → `quality_gates`. Weight tabloları gate key kabul etmez (FK).
- Nota key’leri snake_case (`warm_spicy`); scrape `accord_breakdown` boşluklu (`warm spicy`) → `note_aliases` ile eşle.
- Wheel’de olmayan accord’lar (örn. `metallic`) bilinçli unmapped kalabilir; şema sonundaki bakım sorgusunu kullan.

---

## Agent kuralları

1. **Şema:** Değişiklik önce `SQL_SCHEMAS.MD`, sonra `migrations/00N_*.sql`, sonra **`SQL_HISTORY.MD` kaydı**. Kolonları sessiz rename etme.
2. **Migration numarası:** Sıradaki boş `00N`; geriye dönük dosyayı rewrite etme (gerekirse yeni migration).
3. **Scrape:** Encrypted blob → `utils/fragrantica_crypto.py`; passphrase site rebuild’de dönebilir.
4. **Auth:** Scrape route’ları Bearer JWT (`utils/auth.py`).
5. **Reviews:** Perfume’da `fragrantica_id` + `perfume_url` şart; rate limit / Cloudflare 403 mümkün.
6. **Similar:** JSONB kalır; ayrı tablo ekleme.
7. **Üst katman (1–6 / 1b):** Migration yoksa “yok” say; scrape ile karıştırma. Gate’i mood weight’e ekleme.
8. **Dokümantasyon:** Migration eklerken `SQL_HISTORY.MD` + bu dosyanın özet tablosunu güncelle.
9. **Şema sürümü:** `SQL_SCHEMAS.MD` v0.3 — eski `mood_axes.is_gate` / 10’lu uydurma `note_categories` modelini kullanma.

---

## Hızlı API referansı

| Endpoint | Auth | Ne yapar |
|----------|------|----------|
| `GET /perfumes`, `GET /perfumes/{id}` | Hayır | Perfume oku |
| `GET /perfumes/{id}/reviews` | Hayır | Kayıtlı review’lar |
| `POST /scrape/*` | Evet | Perfume scrape |
| `POST /scrape/reviews/{id}?pages=&sentiment=` | Evet | Review scrape (default pages=5, sentiment=positive) |
