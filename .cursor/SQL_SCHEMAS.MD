-- =====================================================================
-- AKORD — TAM ŞEMA (tüm katmanlar, tek dosya) — v0.3
-- PostgreSQL 14+
--
-- v0.3 DEĞİŞİKLİKLERİ:
--   - GATE'LER AYRI TABLOYA TAŞINDI (quality_gates). Önceki sürümde
--     mood_axes içinde is_gate=true diye işaretli 2 satır vardı
--     (gate_disgust, gate_valence) — bu KAFA KARIŞTIRICIYDI, çünkü aynı
--     tabloda "moda karışan eksen" ile "hiç modla ilgisi olmayan filtre"
--     bir arada duruyordu. Şimdi yapısal olarak ayrılar: mood_axes
--     SADECE modlara karışan 6 ekseni içeriyor, mood_label_weights ve
--     note_category_axis_weights SADECE mood_axes'e FK verebiliyor —
--     yani bir gate'i yanlışlıkla bir moda karıştırmak artık FK
--     seviyesinde imkansız, sadece yorum satırına güvenmiyoruz.
--   - note_categories, Fragrantica'nın kendi nota tekerleğinden gerçek
--     30 kategoriyle (ve gerçek renkleriyle) dolduruldu — önceki 10
--     kategorilik uydurma liste kaldırıldı.
--
-- OKUMA REHBERİ:
--   0) VERİ (senin mevcut tabloların)         → ZORUNLU, dokunulmadı
--   1) MOD REÇETESİ (mood_axes — sadece 6)     → ZORUNLU
--   1b) KALİTE FİLTRELERİ (quality_gates)      → ZORUNLU ama HİÇBİR MODA KARIŞMAZ
--   2) NOTA TAKSONOMİSİ (gerçek 30 kategori)   → ZORUNLU
--   3) SÖZLÜK (emotion_lexicon)                → OPSİYONEL
--   4) SKOR ÇIKTISI                            → ZORUNLU
--   5) TİCARİ / AFFILIATE                      → ZORUNLU
--   6) UYGULAMA (kullanıcı seçimleri)           → ZORUNLU
-- =====================================================================

create extension if not exists pgcrypto;
create extension if not exists pg_trgm;


-- =====================================================================
-- 0) VERİ — senin mevcut tabloların, aynen
-- =====================================================================

create table if not exists perfumes (
    id uuid default gen_random_uuid() primary key,
    name text not null,
    brand text,
    release_year integer,
    gender text,
    fragrantica_id integer,
    fragrance_family text,
    perfumer text,
    main_accords text[],
    accord_breakdown jsonb,
    notes_top text[],
    notes_middle text[],
    notes_base text[],
    rating real,
    votes integer,
    rating_breakdown jsonb,
    when_to_wear jsonb,
    longevity text,
    sillage text,
    longevity_breakdown jsonb,
    sillage_breakdown jsonb,
    price_value jsonb,
    gender_votes jsonb,
    ownership jsonb,
    pros jsonb,
    cons jsonb,
    similar_perfumes jsonb,
    description text,
    image_url text,
    image_url_og text,
    perfume_url text unique,
    created_at timestamptz default timezone('utc', now())
);

create unique index if not exists idx_perfumes_fragrantica_id on perfumes (fragrantica_id) where fragrantica_id is not null;
create index if not exists idx_perfumes_url on perfumes (perfume_url);
create index if not exists idx_perfumes_brand on perfumes (brand);
create index if not exists idx_perfumes_name on perfumes (name);
create index if not exists idx_perfumes_created_at on perfumes (created_at desc);
create index if not exists idx_perfumes_fragrance_family on perfumes (fragrance_family);
create index if not exists idx_perfumes_perfumer on perfumes (perfumer);
create index if not exists idx_perfumes_rating_breakdown on perfumes using gin (rating_breakdown);
create index if not exists idx_perfumes_when_to_wear on perfumes using gin (when_to_wear);

create table if not exists reviews (
    id uuid default gen_random_uuid() primary key,
    perfume_id uuid not null references perfumes(id) on delete cascade,
    fragrantica_review_id bigint not null unique,
    fragrantica_perfume_id integer,
    sentiment text not null,
    username text,
    user_id integer,
    content_html text,
    content_text text,
    vote_yes integer,
    vote_no integer,
    karma_score real,
    review_date timestamptz,
    perfume_votes jsonb,
    member_url text,
    avatar_url text,
    created_at timestamptz default timezone('utc', now()),
    updated_at timestamptz default timezone('utc', now())
);

create index if not exists idx_reviews_perfume_id on reviews (perfume_id);
create index if not exists idx_reviews_sentiment on reviews (sentiment);
create index if not exists idx_reviews_review_date on reviews (review_date desc);
create index if not exists idx_reviews_text_trgm on reviews using gin (content_text gin_trgm_ops);


-- =====================================================================
-- 1) MOD REÇETESİ — SADECE modlara karışan 6 eksen. Gate YOK burada.
-- =====================================================================

create table mood_axes (
    key         text primary key,   -- 'happiness','sensuality','energy','soothing','edibility','dominance' — SADECE BUNLAR
    labels      jsonb not null,
    source_ref  text not null
);

create table mood_labels (
    key      text primary key,
    labels   jsonb not null,
    version  text not null default 'v0.1'
);

-- Sadece mood_axes'e FK veriyor — bir gate'i buraya eklemeye çalışırsan
-- veritabanı hata verir, yanlışlıkla mode karıştırmak yapısal olarak
-- imkansız.
create table mood_label_weights (
    mood_key  text not null references mood_labels(key),
    axis_key  text not null references mood_axes(key),
    weight    numeric(5,2) not null check (weight between 0 and 100),
    primary key (mood_key, axis_key)
);


-- =====================================================================
-- 1b) KALİTE FİLTRELERİ — mood_axes'ten TAMAMEN AYRI bir tablo.
--
--     Bunlar kullanıcının arayacağı bir mod DEĞİL. Kimse "tiksindirici
--     parfüm" aramaz — haklısın. Bu ikisi sadece pipeline'ın içinde,
--     bir parfümü herhangi bir moda güvenle atamadan önce çalışan bir
--     kalite kontrolü: yorumlarda/pros-cons'ta tiksinti dili yüksekse
--     ya da genel valans düşükse, sistem parfümü moddan düşürür ya da
--     "kutuplaştırıcı" diye ayrı işaretler. HİÇBİR mood_label_weights
--     satırında bu tablodan bir key göremezsin — çünkü mood_label_weights
--     yukarıda sadece mood_axes'e FK veriyor, bu tabloya değil.
-- =====================================================================

create table quality_gates (
    key             text primary key,   -- 'disgust', 'valence'
    labels          jsonb not null,
    source_ref      text not null,
    description     text not null       -- pipeline'da tam olarak ne yaptığı
);

insert into quality_gates (key, labels, source_ref, description) values
    ('disgust', '{"en":{"label":"Disgust / Irritation"},"tr":{"label":"Tiksinti / Rahatsızlık"}}',
     'GEOS (Chrea et al. 2009)',
     'rating_breakdown''daki hate+dislike oranı ya da metindeki tiksinti dili bir eşiği aşarsa perfume_mood_scores.gated_out = true yapılır. Hiçbir moda pozitif katkı vermez.'),
    ('valence', '{"en":{"label":"Valence (general positivity)"},"tr":{"label":"Valans (genel olumluluk)"}}',
     'VAD-text (Warriner et al. 2013)',
     'rating_breakdown''daki love+like oranı düşükse tüm mod skorları bir çarpanla bastırılır (confidence düşürülür). Hiçbir moda pozitif katkı vermez.');


-- =====================================================================
-- 2) NOTA TAKSONOMİSİ — Fragrantica'nın kendi nota tekerleğinden
--    gerçek 30 kategori + gerçek renkler.
-- =====================================================================

create table note_categories (
    key          text primary key,
    labels       jsonb not null,
    wheel_order  smallint not null,
    wheel_color  text not null
);

-- Ham metni (accord_breakdown key'i YA DA notes_* elemanı) kategoriye
-- bağlar. 30 kategorinin çoğu accord_breakdown'daki isimle birebir
-- aynı (örn. "woody" -> woody) — o satırlar birebir eşleme (identity).
-- notes_top/middle/base'deki gerçek malzeme isimleri (örn. "Saffron")
-- ve nadir görülen/tekerlekte olmayan accord'lar (örn. "metallic" —
-- BR540'ta gördüğümüz gibi, 30'luk resmi listede YOK) için gerçek bir
-- eşleme kararı gerekiyor.
create table note_aliases (
    id                 uuid primary key default gen_random_uuid(),
    note_category_key  text not null references note_categories(key),
    raw_text           text not null,
    unique (raw_text)
);

create index note_aliases_raw_text_idx on note_aliases (raw_text);

create table note_category_axis_weights (
    note_category_key  text not null references note_categories(key),
    axis_key            text not null references mood_axes(key),
    weight               numeric(5,2) not null,
    rationale            text,
    primary key (note_category_key, axis_key)
);


-- =====================================================================
-- 3) SÖZLÜK — OPSİYONEL
-- =====================================================================

create table emotion_lexicon (
    id         uuid primary key default gen_random_uuid(),
    word       text not null,
    language   text not null,
    valence    numeric(4,2),   -- NRC-VAD raw −1..+1 (app rescales to 0–100 at score time)
    arousal    numeric(4,2),   -- stored; unused in current mood blend
    dominance  numeric(4,2),
    source     text not null,
    unique (word, language, source)
);


-- =====================================================================
-- 4) SKOR ÇIKTISI
-- =====================================================================

create table perfume_mood_scores (
    id             uuid primary key default gen_random_uuid(),
    perfume_id     uuid not null references perfumes(id) on delete cascade,
    mood_key       text not null references mood_labels(key),
    score          numeric(5,2) not null,
    sample_size    integer not null default 0,
    confidence     text not null check (confidence in ('low_prior','medium','high')),
    gated_out      boolean not null default false,   -- quality_gates pipeline'da bunu set eder
    computed_at    timestamptz not null default now(),
    unique (perfume_id, mood_key)
);

create index perfume_mood_scores_perfume_idx on perfume_mood_scores (perfume_id);

-- OPSİYONEL. axis_key'e bilinçli olarak FK yok: hem mood_axes'ten (6
-- eksen) hem quality_gates'ten (2 filtre) bir key gelebilir — LLM aynı
-- review'ı hem 6 eksende hem 2 gate'te puanlıyor. Uygulama katmanında
-- doğrula.
create table review_axis_scores (
    id             uuid primary key default gen_random_uuid(),
    review_id      uuid not null references reviews(id) on delete cascade,
    axis_key       text not null,      -- mood_axes.key ∪ quality_gates.key
    score          numeric(5,2) not null,
    method         text not null check (method in ('lexicon','llm')),
    computed_at    timestamptz not null default now(),
    unique (review_id, axis_key, method)
);

create index review_axis_scores_review_idx on review_axis_scores (review_id);

-- Fragrantica pros/cons (character-relevant only) — blended into mood posteriors
-- alongside reviews. axis_key FK’siz (mood ∪ gate), aynı review_axis_scores deseni.
create table perfume_opinion_scores (
    id                 uuid primary key default gen_random_uuid(),
    perfume_id         uuid not null references perfumes(id) on delete cascade,
    opinion_type       text not null check (opinion_type in ('pro', 'con')),
    opinion_text       text not null,
    fragrantica_score  integer not null,
    axis_key           text not null,
    score              numeric(5,2) not null,
    method             text not null check (method in ('lexicon', 'llm')),
    computed_at        timestamptz not null default now(),
    unique (perfume_id, opinion_text, axis_key, method)
);

create index perfume_opinion_scores_perfume_idx on perfume_opinion_scores (perfume_id);


-- =====================================================================
-- 5) TİCARİ / AFFILIATE
-- =====================================================================

create table retailers (
    id                 uuid primary key default gen_random_uuid(),
    name               text not null unique,
    country            text,
    website            text,
    affiliate_network  text,
    affiliate_tag      text
);

create table purchase_links (
    id                uuid primary key default gen_random_uuid(),
    perfume_id        uuid not null references perfumes(id) on delete cascade,
    retailer_id       uuid not null references retailers(id),
    url               text not null,
    is_affiliate      boolean not null default true,
    region            text,
    size_ml           smallint,
    price             numeric(10,2),
    currency          text not null,
    status            text not null default 'active' check (status in ('active','dead','out_of_stock')),
    last_checked_at   timestamptz,
    created_at        timestamptz not null default now()
);

create index purchase_links_perfume_idx on purchase_links (perfume_id);

create table link_clicks (
    id                 uuid primary key default gen_random_uuid(),
    purchase_link_id   uuid not null references purchase_links(id),
    session_id         uuid,
    referrer_mood_key  text references mood_labels(key),
    clicked_at         timestamptz not null default now()
);


-- =====================================================================
-- 6) UYGULAMA
-- =====================================================================

create table user_selections (
    id                 uuid primary key default gen_random_uuid(),
    session_id         uuid not null,
    note_category_key  text not null references note_categories(key),
    intensity          smallint not null check (intensity between 1 and 10),
    created_at         timestamptz not null default now()
);

create table user_mood_selections (
    session_id  uuid not null,
    mood_key    text not null references mood_labels(key),
    created_at  timestamptz not null default now(),
    primary key (session_id, mood_key)
);


-- =====================================================================
-- ÖRNEK VERİ
-- =====================================================================

insert into mood_axes (key, labels, source_ref) values
    ('happiness',  '{"en":{"label":"Happiness / Well-being"},"tr":{"label":"Mutluluk / İyi Oluş"}}', 'GEOS (Chrea et al. 2009)'),
    ('sensuality', '{"en":{"label":"Sensuality / Desire"},"tr":{"label":"Duyusallık / Arzu"}}',        'GEOS (Chrea et al. 2009)'),
    ('energy',     '{"en":{"label":"Energy"},"tr":{"label":"Enerji"}}',                                 'GEOS (Chrea et al. 2009)'),
    ('soothing',   '{"en":{"label":"Soothing / Peacefulness"},"tr":{"label":"Sakinleşme / Huzur"}}',    'GEOS (Chrea et al. 2009)'),
    ('edibility',  '{"en":{"label":"Gourmand / Warmth"},"tr":{"label":"Gurme / Sıcaklık"}}',            'GEOS (Chrea et al. 2009)'),
    ('dominance',  '{"en":{"label":"Control / Power"},"tr":{"label":"Kontrol / Güç"}}',                 'VAD-text (Warriner et al. 2013)');

insert into mood_labels (key, labels) values
    ('powerful',  '{"en":{"label":"Powerful","tagline":"Fills the room, does not step back"},"tr":{"label":"Güçlü / Otoriter","tagline":"Girdiği odada fark edilen, geri çekilmeyen duruş"}}'),
    ('confident', '{"en":{"label":"Confident","tagline":"Control and well-being combined"},"tr":{"label":"Özgüvenli","tagline":"Kontrol hissiyle memnuniyetin karışımı"}}'),
    ('grounded',  '{"en":{"label":"Grounded","tagline":"Understated but trustworthy"},"tr":{"label":"Ayakları Yere Basan","tagline":"Gösterişsiz ama güvenilir, doğal duruş"}}'),
    ('energetic', '{"en":{"label":"Energetic","tagline":"Revitalizing, ready to move"},"tr":{"label":"Enerjik","tagline":"Canlanma, harekete geçme"}}'),
    ('calm',      '{"en":{"label":"Calm","tagline":"Tension release, relaxation"},"tr":{"label":"Sakin / Huzurlu","tagline":"Gerginliğin azalması, rahatlama"}}'),
    ('elegant',   '{"en":{"label":"Elegant","tagline":"Composed refinement"},"tr":{"label":"Zarif / Sofistike","tagline":"Duruşlu, gösterişten kaçınan incelik"}}'),
    ('romantic',  '{"en":{"label":"Romantic","tagline":"Seductive, intimate"},"tr":{"label":"Romantik / Duyusal","tagline":"Baştan çıkarıcı, yakınlık hissi"}}'),
    ('cozy',      '{"en":{"label":"Warm / Cozy","tagline":"Home, safety, intimacy"},"tr":{"label":"Sıcak / Samimi","tagline":"Ev, güvenli alan, yakınlık hissi"}}');

insert into mood_label_weights (mood_key, axis_key, weight) values
    ('powerful','dominance',55), ('powerful','energy',30), ('powerful','sensuality',15),
    ('confident','dominance',45), ('confident','happiness',30), ('confident','energy',25),
    ('grounded','soothing',45), ('grounded','dominance',30), ('grounded','happiness',25),
    ('energetic','energy',70), ('energetic','happiness',15), ('energetic','dominance',15),
    ('calm','soothing',75), ('calm','happiness',25),
    ('elegant','sensuality',40), ('elegant','happiness',30), ('elegant','dominance',30),
    ('romantic','sensuality',65), ('romantic','happiness',35),
    ('cozy','edibility',40), ('cozy','soothing',35), ('cozy','happiness',25);

-- note_categories — Fragrantica'nın kendi tekerleğinden, gerçek 30
-- kategori + gerçek renkler (senin paylaştığın dropdown'dan).
insert into note_categories (key, labels, wheel_order, wheel_color) values
    ('powdery',     '{"en":{"label":"Powdery"},"tr":{"label":"Pudralı"}}',       0, '#EEDDCC'),
    ('woody',       '{"en":{"label":"Woody"},"tr":{"label":"Odunsu"}}',         1, '#774414'),
    ('aromatic',    '{"en":{"label":"Aromatic"},"tr":{"label":"Aromatik"}}',    2, '#37A089'),
    ('citrus',      '{"en":{"label":"Citrus"},"tr":{"label":"Narenciye"}}',     3, '#F9FF52'),
    ('fresh_spicy', '{"en":{"label":"Fresh Spicy"},"tr":{"label":"Taze Baharatlı"}}', 4, '#83C928'),
    ('animalic',    '{"en":{"label":"Animalic"},"tr":{"label":"Hayvansı"}}',    5, '#8E4B13'),
    ('balsamic',    '{"en":{"label":"Balsamic"},"tr":{"label":"Balzamik"}}',    6, '#AD8359'),
    ('warm_spicy',  '{"en":{"label":"Warm Spicy"},"tr":{"label":"Sıcak Baharatlı"}}', 7, '#CC3300'),
    ('green',       '{"en":{"label":"Green"},"tr":{"label":"Yeşil"}}',          8, '#0E8C1D'),
    ('fresh',       '{"en":{"label":"Fresh"},"tr":{"label":"Ferah"}}',          9, '#9BE5ED'),
    ('musky',       '{"en":{"label":"Musky"},"tr":{"label":"Miskli"}}',        10, '#E7D8EA'),
    ('fruity',      '{"en":{"label":"Fruity"},"tr":{"label":"Meyvemsi"}}',     11, '#FC4B29'),
    ('soft_spicy',  '{"en":{"label":"Soft Spicy"},"tr":{"label":"Yumuşak Baharatlı"}}', 12, '#E27752'),
    ('amber',       '{"en":{"label":"Amber"},"tr":{"label":"Amber"}}',         13, '#BC4D10'),
    ('earthy',      '{"en":{"label":"Earthy"},"tr":{"label":"Topraksı"}}',     14, '#544838'),
    ('white_floral','{"en":{"label":"White Floral"},"tr":{"label":"Beyaz Çiçek"}}', 15, '#EDF2FB'),
    ('vanilla',     '{"en":{"label":"Vanilla"},"tr":{"label":"Vanilya"}}',     16, '#FFFEC0'),
    ('rose',        '{"en":{"label":"Rose"},"tr":{"label":"Gül"}}',            17, '#FE016B'),
    ('herbal',      '{"en":{"label":"Herbal"},"tr":{"label":"Bitkisel"}}',     18, '#6CA47F'),
    ('patchouli',   '{"en":{"label":"Patchouli"},"tr":{"label":"Patchouli"}}', 19, '#63652E'),
    ('lactonic',    '{"en":{"label":"Lactonic"},"tr":{"label":"Sütsü"}}',      20, '#FBF9F2'),
    ('aldehydic',   '{"en":{"label":"Aldehydic"},"tr":{"label":"Aldehidik"}}', 21, '#D8E9F6'),
    ('aquatic',     '{"en":{"label":"Aquatic"},"tr":{"label":"Sucul"}}',       22, '#63CCE2'),
    ('smoky',       '{"en":{"label":"Smoky"},"tr":{"label":"Dumanlı"}}',       23, '#827487'),
    ('leather',     '{"en":{"label":"Leather"},"tr":{"label":"Deri"}}',        24, '#78483A'),
    ('soapy',       '{"en":{"label":"Soapy"},"tr":{"label":"Sabunlu"}}',       25, '#E3F6FC'),
    ('violet',      '{"en":{"label":"Violet"},"tr":{"label":"Menekşe"}}',      26, '#9C1DFF'),
    ('sour',        '{"en":{"label":"Sour"},"tr":{"label":"Ekşi"}}',           27, '#C0E741'),
    ('ozonic',      '{"en":{"label":"Ozonic"},"tr":{"label":"Ozonik"}}',       28, '#C9FDFB'),
    ('tropical',    '{"en":{"label":"Tropical"},"tr":{"label":"Tropikal"}}',   29, '#F6AF09');

-- note_category_axis_weights — 30 kategorinin TAMAMI için başlangıç
-- tahmini var (aksi halde pipeline yarım kalır), AMA HİÇBİRİ kalibre
-- edilmedi — bunlar benim GEOS/VAD tanımlarına bakarak yaptığım
-- yönlü tahminler, bir çalışmadan alınmadı. İnsan-puanlı örneklemle
-- doğrulanmadan production kararı vermek için kullanma.
insert into note_category_axis_weights (note_category_key, axis_key, weight, rationale) values
    ('powdery',      'soothing',   60, 'Yumuşak, saran his — kalibre edilmedi'),
    ('powdery',      'sensuality', 20, 'Kalibre edilmedi'),
    ('woody',        'dominance',  55, 'Zarzo & Stanton (2009) haritasında ağır/sıcak uca yakın — kalibre edilmedi'),
    ('woody',        'soothing',   20, 'Kalibre edilmedi'),
    ('aromatic',     'energy',     45, 'Bitkisel/ferah karakter — kalibre edilmedi'),
    ('aromatic',     'soothing',   25, 'Kalibre edilmedi'),
    ('citrus',       'energy',     65, 'Zarzo (2008) "tazelik" ekseninde en yüksek uçta — kalibre edilmedi'),
    ('citrus',       'happiness',  20, 'Kalibre edilmedi'),
    ('fresh_spicy',  'energy',     50, 'Kalibre edilmedi'),
    ('fresh_spicy',  'dominance',  20, 'Kalibre edilmedi'),
    ('animalic',     'sensuality', 60, 'Primal/cinsel çağrışım klasik parfümeride yaygın — kalibre edilmedi'),
    ('animalic',     'dominance',  25, 'Kalibre edilmedi'),
    ('balsamic',     'soothing',   40, 'Reçineli/sıcak his — kalibre edilmedi'),
    ('balsamic',     'edibility',  30, 'Kalibre edilmedi'),
    ('warm_spicy',   'dominance',  40, 'Kalibre edilmedi'),
    ('warm_spicy',   'sensuality', 25, 'Kalibre edilmedi'),
    ('green',        'energy',     40, 'Kalibre edilmedi'),
    ('green',        'soothing',   30, 'Kalibre edilmedi'),
    ('fresh',        'energy',     55, 'Kalibre edilmedi'),
    ('fresh',        'happiness',  25, 'Kalibre edilmedi'),
    ('musky',        'sensuality', 55, 'Kalibre edilmedi'),
    ('musky',        'soothing',   25, 'Kalibre edilmedi'),
    ('fruity',       'happiness',  55, 'Kalibre edilmedi'),
    ('fruity',       'energy',     20, 'Kalibre edilmedi'),
    ('soft_spicy',   'sensuality', 35, 'Kalibre edilmedi'),
    ('soft_spicy',   'soothing',   30, 'Kalibre edilmedi'),
    ('amber',        'sensuality', 50, 'Oryantal/amber ailesi klasik olarak duyusallıkla ilişkilendirilir — kalibre edilmedi'),
    ('amber',        'edibility',  30, 'Amber genelde vanilya/şeker gibi gurme notalarla birlikte görülür — kalibre edilmedi'),
    ('earthy',       'dominance',  45, 'Kalibre edilmedi'),
    ('earthy',       'soothing',   30, 'Kalibre edilmedi'),
    ('white_floral', 'sensuality', 45, 'Beyaz çiçek ailesi (jasmine vb.) duyusallıkla ilişkili — kalibre edilmedi'),
    ('white_floral', 'happiness',  25, 'Kalibre edilmedi'),
    ('vanilla',      'edibility',  65, 'Kalibre edilmedi'),
    ('vanilla',      'happiness',  20, 'Kalibre edilmedi'),
    ('rose',         'sensuality', 50, 'Kalibre edilmedi'),
    ('rose',         'happiness',  30, 'Kalibre edilmedi'),
    ('herbal',       'soothing',   45, 'Kalibre edilmedi'),
    ('herbal',       'energy',     25, 'Kalibre edilmedi'),
    ('patchouli',    'dominance',  40, 'Kalibre edilmedi'),
    ('patchouli',    'sensuality', 30, 'Kalibre edilmedi'),
    ('lactonic',     'soothing',   40, 'Kremsi/sütsü his — kalibre edilmedi'),
    ('lactonic',     'edibility',  30, 'Kalibre edilmedi'),
    ('aldehydic',    'energy',     35, 'Klasik "parlak/sabunlu" aldehid etkisi — kalibre edilmedi'),
    ('aldehydic',    'happiness',  30, 'Kalibre edilmedi'),
    ('aquatic',      'energy',     55, 'Kalibre edilmedi'),
    ('aquatic',      'soothing',   25, 'Kalibre edilmedi'),
    ('smoky',        'dominance',  50, 'Kalibre edilmedi'),
    ('smoky',        'sensuality', 20, 'Kalibre edilmedi'),
    ('leather',      'dominance',  60, 'Kalibre edilmedi'),
    ('leather',       'sensuality', 20, 'Kalibre edilmedi'),
    ('soapy',        'soothing',   50, 'Kalibre edilmedi'),
    ('soapy',        'happiness',  25, 'Kalibre edilmedi'),
    ('violet',       'sensuality', 35, 'Pudralı çiçeksi — kalibre edilmedi'),
    ('violet',       'happiness',  30, 'Kalibre edilmedi'),
    ('sour',         'energy',     40, 'Kalibre edilmedi'),
    ('sour',         'dominance',  15, 'Kalibre edilmedi'),
    ('ozonic',       'energy',     50, 'Kalibre edilmedi'),
    ('ozonic',       'soothing',   25, 'Kalibre edilmedi'),
    ('tropical',     'happiness',  55, 'Kalibre edilmedi'),
    ('tropical',     'energy',     25, 'Kalibre edilmedi');

-- note_aliases — resmi 30 kategorinin BÜYÜK KISMI accord_breakdown'daki
-- isimle birebir aynı (identity mapping). BR540'ın gerçek verisinden:
insert into note_aliases (note_category_key, raw_text) values
    ('woody','woody'), ('amber','amber'), ('warm_spicy','warm spicy'),
    ('fresh_spicy','fresh spicy'), ('aromatic','aromatic'),
    ('white_floral','white floral'), ('animalic','animalic');
    -- DİKKAT: BR540'ın accord_breakdown'ında "metallic" de var, ama
    -- resmi 30 kategoride YOK. Bilinçli olarak eşlenmedi — bu satır
    -- yorum olarak kalsın, "metallic" senin karar vermen gereken bir
    -- edge case (en yakın aday muhtemelen 'aldehydic' ya da 'smoky',
    -- ama bu bir tahmin, sen karar ver).

-- notes_top/middle/base'deki gerçek malzeme isimleri için örnek eşleme.
-- NOT: resmi 30 kategoride ayrı bir "gourmand" kovası yok — en yakını
-- vanilla/lactonic/balsamic. "Sugar" bu yüzden vanilla'ya bağlandı.
insert into note_aliases (note_category_key, raw_text) values
    ('warm_spicy', 'Saffron'),
    ('white_floral', 'Jasmine'),
    ('amber', 'Amberwood'),
    ('amber', 'Ambergris'),
    ('white_floral', 'Hedione'),
    ('balsamic', 'Fir Resin'),
    ('woody', 'Cedar'),
    ('vanilla', 'Sugar'),
    ('amber', 'Ambroxan'),
    ('earthy', 'Oakmoss');

insert into retailers (name, country, website, affiliate_network, affiliate_tag) values
    ('FragranceX', 'US', 'https://www.fragrancex.com', 'CJ Affiliate', 'akord-cj-000'),
    ('Notino', 'CZ', 'https://www.notino.com', 'Awin', 'akord-awin-000');


-- =====================================================================
-- BAKIM SORGUSU — hangi ham nota/accord metni hiç kategoriye
-- bağlanmamış? Yeni parfüm ekledikçe bunu çalıştır, çıkanları
-- note_aliases'a ekle.
-- =====================================================================
-- select distinct k as unmapped_raw_text
-- from perfumes, jsonb_object_keys(accord_breakdown) as k
-- where not exists (select 1 from note_aliases where raw_text = k)
-- union
-- select distinct n as unmapped_raw_text
-- from perfumes, unnest(notes_top || notes_middle || notes_base) as n
-- where not exists (select 1 from note_aliases where raw_text = n);