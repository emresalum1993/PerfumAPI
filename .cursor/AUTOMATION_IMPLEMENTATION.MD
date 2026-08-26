# Akord — Mood Scoring Pipeline (Otomasyon Planı)

**Status (2026-08-26):** Implemented in `pipeline/` + `POST /pipeline/*` (see `AGENTS.md`). LLM via OmniRoute (`LLM_BASE_URL`).

## Tek endpoint mi, ayrı ayrı mı?

**Ayrı ayrı endpoint'ler + üstte ince bir orchestrator.** Sebep: aşağıdaki 3 adımın maliyet/hata profili çok farklı.

- Nota eşleme kontrolü → saniyeler, ücretsiz, sık çalıştırılabilir
- Review skorlama → **LLM çağrısı = pahalı + yavaş + timeout/hata riski var**, idempotent ve resumable olmalı
- Mood hesaplama → saf SQL, çok ucuz, review geldikçe defalarca yeniden çalıştırılacak

Review skorlamayı mood hesaplamayla aynı endpoint'e koyarsan, sadece yeni review geldi diye her seferinde LLM'i tekrar tetikleme riski doğar (para kaybı). Ayrı tutunca, mood'u istediğin kadar ucuza yeniden hesaplarsın, LLM'i sadece skorlanmamış review varsa çağırırsın.

Üstte tek bir `run-all` endpoint'i olsun ama o sadece 3'ünü sırayla çağıran ince bir orkestratör olsun — cron/otomasyon ondan tetiklenir, ama debug/yeniden çalıştırma ihtiyacında adımları tek tek de çağırabilirsin.

---

## Endpoint 1 — `POST /pipeline/notes/check-unmapped`

**Ne yapar:** Tüm `perfumes.accord_breakdown` key'lerini ve `notes_top/middle/base` elemanlarını `note_aliases`'a karşı kontrol eder, eşlenmemiş ham metinleri döner (şemadaki bakım sorgusu zaten hazır).

**Girdi:** yok. **Çıktı:** `["metallic", ...]` gibi bir liste.

**Not:** Bu bir *gate*'tir — eşlenmemiş varsa `run-all` ya durur ya da uyarıp o perfume'leri atlar (`force=true` ile bypass edilebilir).

---

## Endpoint 2 — `POST /pipeline/reviews/score`

**Ne yapar:** `review_axis_scores`'ta karşılığı olmayan (yani `method='llm'` ile henüz skorlanmamış) her review için tek bir LLM çağrısı yapar; prompt GEOS rubric'ini verir, review 8 değer için puanlanır (6 mood ekseni + 2 gate: disgust, valence) — tek review = tek LLM çağrısı, 8 ayrı çağrı değil. Sonuçları `review_axis_scores`'a insert eder.

**Girdi (opsiyonel):** `perfume_id` (verilmezse tüm perfume'lerdeki skorlanmamış review'lar), `limit` (batch boyutu, rate-limit için), `rescore` (true ise önce o perfume'nin LLM skorlarını sil — **`perfume_id` zorunlu**).

**Kritik:** Idempotent olmalı — zaten skorlanmış review'ı atla (`unique(review_id, axis_key, method)`). `rescore=true` ile bilinçli wipe + yeniden skor. Hata/timeout olursa kaldığı yerden devam edebilmeli.

---

## Endpoint 3 — `POST /pipeline/moods/compute`

**Ne yapar:** Her perfume için:
1. Nota bazlı prior'u hesapla (`accord_breakdown → note_aliases → note_category_axis_weights` join, yüzde ağırlıklı)
2. `review_axis_scores`'tan mood eksenlerinin posterior ortalamasını al (6 eksen), gate'lerin (disgust, valence) ayrı ortalamasını al
3. Blend: `final = prior*(1-w) + posterior*w`, `w = min(1, review_count/threshold)`
4. Gate uygula: disgust yüksekse `gated_out=true`; valence düşükse `confidence` bir kademe düşür — **bu adım sadece quality_gates'ten okur, mood_axes'e hiç dokunmaz**
5. `mood_label_weights` ile 6 eksen skorunu 8 mood etiketine çevir, `perfume_mood_scores`'a upsert et

**Girdi (opsiyonel):** `perfume_id` (verilmezse tüm perfume'ler). **Çıktı:** kaç perfume güncellendi.

**Not:** Saf SQL/hesaplama, LLM çağırmaz — bu yüzden istediğin kadar sık (örn. her gece cron ile) tekrar çalıştırılabilir.

---

## Orchestrator — `POST /pipeline/run-all`

Sırayla: 1 → (unmapped varsa dur/uyar) → 2 (tüm perfume'ler, skorlanmamış review'lar) → 3 (tüm perfume'ler). Cron/scheduled job bunu tetikler. Tek tek adımları debug/yeniden çalıştırmak için endpoint 1-2-3'ü ayrı ayrı da çağırabilirsin.