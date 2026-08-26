-- Migration: Create reviews table for Fragrantica perfume reviews
-- Run in Supabase SQL Editor after 001–003

CREATE TABLE IF NOT EXISTS reviews (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    perfume_id UUID NOT NULL REFERENCES perfumes(id) ON DELETE CASCADE,
    fragrantica_review_id BIGINT NOT NULL,
    fragrantica_perfume_id INTEGER,
    sentiment TEXT NOT NULL,
    username TEXT,
    user_id INTEGER,
    content_html TEXT,
    content_text TEXT,
    vote_yes INTEGER,
    vote_no INTEGER,
    karma_score REAL,
    review_date TIMESTAMP WITH TIME ZONE,
    perfume_votes JSONB,
    member_url TEXT,
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW()),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW()),
    UNIQUE (fragrantica_review_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_perfume_id ON reviews(perfume_id);
CREATE INDEX IF NOT EXISTS idx_reviews_sentiment ON reviews(sentiment);
CREATE INDEX IF NOT EXISTS idx_reviews_review_date ON reviews(review_date DESC);

COMMENT ON TABLE reviews IS 'Perfume reviews scraped from Fragrantica reviews4perfume_v2';
COMMENT ON COLUMN reviews.fragrantica_review_id IS 'Fragrantica review id (unique across site)';
COMMENT ON COLUMN reviews.sentiment IS 'Filter used when scraping: positive or negative';
COMMENT ON COLUMN reviews.content_html IS 'Original review HTML from komentar';
COMMENT ON COLUMN reviews.content_text IS 'Plain-text version of the review';
COMMENT ON COLUMN reviews.perfume_votes IS 'Reviewer votes snapshot (rating, seasons, longevity, etc.)';
