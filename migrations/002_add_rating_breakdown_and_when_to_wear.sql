-- Migration 002: Add Fragrantica sentiment + when-to-wear vote breakdowns
-- Run this in Supabase SQL Editor

ALTER TABLE perfumes
    ADD COLUMN IF NOT EXISTS rating_breakdown JSONB,
    ADD COLUMN IF NOT EXISTS when_to_wear JSONB;

COMMENT ON COLUMN perfumes.rating_breakdown IS
    'Sentiment vote breakdown from Fragrantica Rating card, e.g. {"love":{"votes":12800,"percent":43.45},...}';

COMMENT ON COLUMN perfumes.when_to_wear IS
    'Season/time vote breakdown from Fragrantica When To Wear card, e.g. {"winter":{"votes":10000,"percent":100.0},...}';

-- Optional helpers for querying nested vote counts
CREATE INDEX IF NOT EXISTS idx_perfumes_rating_breakdown
    ON perfumes USING GIN (rating_breakdown);

CREATE INDEX IF NOT EXISTS idx_perfumes_when_to_wear
    ON perfumes USING GIN (when_to_wear);
