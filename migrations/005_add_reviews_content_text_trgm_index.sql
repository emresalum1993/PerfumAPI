-- Migration 005: Enable pg_trgm + GIN index on reviews.content_text
-- Run in Supabase SQL Editor after 004
-- Aligns reviews text search with SQL_SCHEMAS.MD (idx_reviews_text_trgm)

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_reviews_text_trgm
    ON reviews USING GIN (content_text gin_trgm_ops);

COMMENT ON INDEX idx_reviews_text_trgm IS
    'Trigram GIN index for ILIKE / similarity search on review plain text';
