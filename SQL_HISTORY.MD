# SQL_HISTORY.MD — Migration geçmişi

Bu dosya, `migrations/` altındaki incremental SQL’lerin **ne yaptığını**, **neden eklendiğini** ve **hangi şema belgesine bağlandığını** kaydeder.

| Belge | Rol |
|-------|-----|
| [`SQL_SCHEMAS.MD`](./SQL_SCHEMAS.MD) | Hedef / tam şema **v0.3** (tek doğruluk kaynağı) |
| [`migrations/`](./migrations/) | Production’a uygulanan adımlar |
| Bu dosya | Her migration’ın geçmişi ve notları |

**Nasıl uygulanır:** Sırayla Supabase SQL Editor’de çalıştır (`001` → `005` …).  
`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` güvenli tekrar çalıştırmaya yakındır; yine de staging’de dene.

Yeni migration eklerken:
1. `migrations/00N_kisa_aciklama.sql` yaz
2. Gerekirse `SQL_SCHEMAS.MD` güncelle
3. **Bu dosyaya** yeni bölüm ekle
4. [`AGENTS.md`](./AGENTS.md) referanslarını güncelle

---

## 001 — `001_create_perfumes_table.sql`

| | |
|---|---|
| **Durum** | Temel tablo |
| **Tarih bağlamı** | Proje başlangıcı — Fragrantica perfume scrape |
| **Ne yaptı** | `perfumes` tablosunu oluşturdu |
| **Neden** | Parfüm master verisini saklamak (isim, marka, notalar, rating, URL) |
| **Eklenen kolonlar** | `id`, `name`, `brand`, `release_year`, `gender`, `notes_top/middle/base`, `rating`, `votes`, `description`, `longevity`, `sillage`, `image_url`, `perfume_url` (UNIQUE), `created_at` |
| **Index’ler** | `idx_perfumes_url`, `idx_perfumes_brand`, `idx_perfumes_name`, `idx_perfumes_created_at` |
| **Bağımlılık** | Yok |
| **Şema eşlemesi** | `SQL_SCHEMAS.MD` katman 0 — `perfumes` çekirdeği |

Not: RLS policy’leri dosyada yorum satırı; production’da ayrıca açılmadıysa public/anon politikaları yok say.

---

## 002 — `002_add_rating_breakdown_and_when_to_wear.sql`

| | |
|---|---|
| **Durum** | Uygulandı (scrape + API kullanıyor) |
| **Ne yaptı** | Sentiment ve “when to wear” oy kırılımlarını ekledi |
| **Neden** | Fragrantica Rating / When to Wear kartlarından gelen histogram’ları saklamak |
| **Eklenen kolonlar** | `rating_breakdown` JSONB, `when_to_wear` JSONB |
| **Index’ler** | GIN: `idx_perfumes_rating_breakdown`, `idx_perfumes_when_to_wear` |
| **Bağımlılık** | `001` |
| **Örnek şekil** | `{"love":{"votes":12800,"percent":43.45},...}` / `{"winter":{"votes":...,"percent":...},...}` |
| **Şema eşlemesi** | `SQL_SCHEMAS.MD` → `perfumes.rating_breakdown`, `when_to_wear` |

---

## 003 — `003_add_extended_perfume_fields.sql`

| | |
|---|---|
| **Durum** | Uygulandı (scrape zengin alanlar) |
| **Ne yaptı** | Status blob + sayfa meta + akort yüzdeleri + benzer/pros-cons alanları |
| **Neden** | SSR’de boş Vue kartları yerine decrypt edilen `status` / `ai_opinions` / `similar_perfumes` ve “Search by accords” URL yüzdeleri |
| **Eklenen kolonlar** | `fragrantica_id`, `fragrance_family`, `perfumer`, `main_accords` TEXT[], `accord_breakdown` JSONB, `longevity_breakdown`, `sillage_breakdown`, `price_value`, `gender_votes`, `ownership`, `pros`, `cons`, `similar_perfumes`, `image_url_og` |
| **Index’ler** | Partial unique `idx_perfumes_fragrantica_id`; `idx_perfumes_fragrance_family`, `idx_perfumes_perfumer` |
| **Bağımlılık** | `001` (+ pratikte `002`) |
| **Önemli** | `accord_breakdown` sonradan aynı dosyaya eklendi — `003`’ü eski haliyle çalıştırdıysan sadece şu SQL’i de uygula: `ADD COLUMN IF NOT EXISTS accord_breakdown JSONB` |
| **Şema eşlemesi** | `SQL_SCHEMAS.MD` katman 0 — genişletilmiş `perfumes` |

---

## 004 — `004_create_reviews_table.sql`

| | |
|---|---|
| **Durum** | Uygulandı |
| **Ne yaptı** | `reviews` tablosunu oluşturdu |
| **Neden** | Fragrantica `reviews4perfume_v2` AJAX yanıtlarını (decrypt sonrası) perfume’a bağlı saklamak; perfume JSONB’sine gömmemek |
| **Eklenen tablo** | `reviews` |
| **Kolonlar** | `id`, `perfume_id` → `perfumes(id)` CASCADE, `fragrantica_review_id` UNIQUE, `fragrantica_perfume_id`, `sentiment`, `username`, `user_id`, `content_html`, `content_text`, `vote_yes/no`, `karma_score`, `review_date`, `perfume_votes`, `member_url`, `avatar_url`, `created_at`, `updated_at` |
| **Index’ler** | `idx_reviews_perfume_id`, `idx_reviews_sentiment`, `idx_reviews_review_date` |
| **Bağımlılık** | `001` (`perfumes` FK) |
| **API** | `POST /scrape/reviews/{perfume_id}`, `GET /perfumes/{id}/reviews` |
| **Şema eşlemesi** | `SQL_SCHEMAS.MD` katman 0 — `reviews` (trgm index hariç; → `005`) |

---

## 005 — `005_add_reviews_content_text_trgm_index.sql`

| | |
|---|---|
| **Durum** | Yeni — SQL Editor’de çalıştır |
| **Ne yaptı** | `pg_trgm` extension + `reviews.content_text` üzerinde GIN trigram index |
| **Neden** | `SQL_SCHEMAS.MD`’deki `idx_reviews_text_trgm` ile hizalamak; review metninde `ILIKE '%…%'` / similarity aramayı hızlandırmak |
| **SQL özeti** | `CREATE EXTENSION IF NOT EXISTS pg_trgm;` + `CREATE INDEX IF NOT EXISTS idx_reviews_text_trgm ON reviews USING GIN (content_text gin_trgm_ops);` |
| **Bağımlılık** | `004` |
| **Not** | Supabase’de `pg_trgm` genelde izinli; extension oluşturma yetkisi yoksa dashboard Extensions’tan aç |
| **Şema eşlemesi** | `SQL_SCHEMAS.MD` v0.3 katman 0 — `idx_reviews_text_trgm` |

---

## Henüz migration olmayan şema (SQL_SCHEMAS.MD v0.3 — 1–6)

Aşağıdakiler tam şemada tanımlı; **ayrı migration dosyası yok** (henüz scrape API’sinin parçası değil).
İlk mood migration’ı yazılırken **v0.3** yapısını kullan (eski `is_gate` / 10 kategori seed’i kopyalama).

| Katman | Tablolar | Ne zaman migrate edilir | v0.3 notu |
|--------|----------|-------------------------|-----------|
| 1 Mod reçetesi | `mood_axes`, `mood_labels`, `mood_label_weights` | Mood motoru | `mood_axes` = 6 eksen; `is_gate` kolonu yok |
| 1b Kalite | `quality_gates` | Mood pipeline | Gate’ler ayrı tablo; weight FK’larına bağlanmaz |
| 2 Nota taksonomisi | `note_categories`, `note_aliases`, `note_category_axis_weights` | Wheel / mapping | **30** Fragrantica wheel kategorisi + seed |
| 3 Sözlük | `emotion_lexicon` | Lexicon yöntemi | Opsiyonel |
| 4 Skor | `perfume_mood_scores`, `review_axis_scores` | Batch skor job | `review_axis_scores.axis_key` FK’siz (mood ∪ gate) |
| 5 Ticari | `retailers`, `purchase_links`, `link_clicks` | Affiliate | — |
| 6 Uygulama | `user_selections`, `user_mood_selections` | Kullanıcı seçimleri | — |

Bunlar için yeni `006+` migration’lar yazıldığında bu dosyaya bölüm ekle.

---

## Sıra özeti

```
001  perfumes (çekirdek)
002  rating_breakdown, when_to_wear
003  extended perfume fields (+ accord_breakdown)
004  reviews
005  reviews.content_text pg_trgm GIN
```
