-- Migration 008: perfume_ai_overviews (LLM synthesis: summary + chip + detail lists)
-- Run in Supabase SQL Editor. Independent of mood scoring tables.

create table if not exists perfume_ai_overviews (
    id                   uuid primary key default gen_random_uuid(),
    perfume_id           uuid not null references perfumes(id) on delete cascade,
    summary              text not null,
    pros                 jsonb not null,   -- 3–5 short chip phrases
    cons                 jsonb not null,
    pros_list            jsonb not null,   -- 6–10 one-sentence detail items
    cons_list            jsonb not null,
    model                text not null,
    input_review_count   integer not null,
    input_opinion_count  integer not null,
    generated_at         timestamptz not null default now(),
    unique (perfume_id)
);

create index if not exists perfume_ai_overviews_perfume_idx
    on perfume_ai_overviews (perfume_id);

comment on table perfume_ai_overviews is
    'Original LLM synthesis of reviews + Fragrantica pros/cons; display cache, upserted on regenerate';
