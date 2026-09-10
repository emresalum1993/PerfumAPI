-- Migration 009: Background-removed thumbnail URL (Supabase Storage public URL)
-- Run in Supabase SQL Editor. Create bucket "perfume-thumbs" (public) in Storage first,
-- or let perfume-api upload (IMAGE_NOBG_UPLOAD=true) create it via service role.

ALTER TABLE perfumes
    ADD COLUMN IF NOT EXISTS image_url_nobg TEXT;

COMMENT ON COLUMN perfumes.image_url_nobg IS
    'Public URL of background-removed PNG in Supabase Storage (perfume-thumbs/{fragrantica_id}.png)';
