-- Migration 003: Extra Fragrantica fields (status blob + page metadata)
-- Run this in Supabase SQL Editor

ALTER TABLE perfumes
    ADD COLUMN IF NOT EXISTS fragrantica_id INTEGER,
    ADD COLUMN IF NOT EXISTS fragrance_family TEXT,
    ADD COLUMN IF NOT EXISTS perfumer TEXT,
    ADD COLUMN IF NOT EXISTS main_accords TEXT[],
    ADD COLUMN IF NOT EXISTS accord_breakdown JSONB,
    ADD COLUMN IF NOT EXISTS longevity_breakdown JSONB,
    ADD COLUMN IF NOT EXISTS sillage_breakdown JSONB,
    ADD COLUMN IF NOT EXISTS price_value JSONB,
    ADD COLUMN IF NOT EXISTS gender_votes JSONB,
    ADD COLUMN IF NOT EXISTS ownership JSONB,
    ADD COLUMN IF NOT EXISTS pros JSONB,
    ADD COLUMN IF NOT EXISTS cons JSONB,
    ADD COLUMN IF NOT EXISTS similar_perfumes JSONB,
    ADD COLUMN IF NOT EXISTS image_url_og TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_perfumes_fragrantica_id
    ON perfumes (fragrantica_id)
    WHERE fragrantica_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_perfumes_fragrance_family
    ON perfumes (fragrance_family);

CREATE INDEX IF NOT EXISTS idx_perfumes_perfumer
    ON perfumes (perfumer);

COMMENT ON COLUMN perfumes.fragrantica_id IS 'Fragrantica numeric perfume id (e.g. 33519)';
COMMENT ON COLUMN perfumes.fragrance_family IS 'Family from description, e.g. Oriental Floral';
COMMENT ON COLUMN perfumes.perfumer IS 'Nose / perfumer name';
COMMENT ON COLUMN perfumes.main_accords IS 'Main accords list from perfume page';
COMMENT ON COLUMN perfumes.accord_breakdown IS 'Main accord percentages parsed from Search by accords URL';
COMMENT ON COLUMN perfumes.longevity_breakdown IS 'Vote histogram for longevity buckets';
COMMENT ON COLUMN perfumes.sillage_breakdown IS 'Vote histogram for sillage buckets';
COMMENT ON COLUMN perfumes.price_value IS 'Price-value average + vote histogram';
COMMENT ON COLUMN perfumes.gender_votes IS 'Community gender perception votes';
COMMENT ON COLUMN perfumes.ownership IS 'have / had / want vote counts';
COMMENT ON COLUMN perfumes.pros IS 'Community pros list with vote scores';
COMMENT ON COLUMN perfumes.cons IS 'Community cons list with vote scores';
COMMENT ON COLUMN perfumes.similar_perfumes IS 'Similar perfume recommendations with votes';
COMMENT ON COLUMN perfumes.image_url_og IS 'OpenGraph / social / full bottle image URL';
