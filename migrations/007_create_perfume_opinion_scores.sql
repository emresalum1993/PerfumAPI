-- Migration 007: perfume_opinion_scores (scored pros/cons for mood blend)
-- Run in Supabase SQL Editor after upper-layer mood tables exist.

create table if not exists perfume_opinion_scores (
    id                 uuid primary key default gen_random_uuid(),
    perfume_id         uuid not null references perfumes(id) on delete cascade,
    opinion_type       text not null check (opinion_type in ('pro', 'con')),
    opinion_text       text not null,
    fragrantica_score  integer not null,
    axis_key           text not null,  -- mood_axes.key ∪ quality_gates.key (no FK)
    score              numeric(5,2) not null,
    method             text not null check (method in ('lexicon', 'llm')),
    computed_at        timestamptz not null default now(),
    unique (perfume_id, opinion_text, axis_key, method)
);

create index if not exists perfume_opinion_scores_perfume_idx
    on perfume_opinion_scores (perfume_id);

comment on table perfume_opinion_scores is
    'LLM/lexicon scores for character-relevant Fragrantica pros/cons; blended into mood posteriors';
