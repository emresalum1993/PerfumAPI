"""Mood scoring pipeline (notes → review/opinion LLM+lexicon → mood compute).

Also exposes AI overview generate/read (separate table; not mood scoring).
"""

from pipeline.notes import check_unmapped
from pipeline.review_scorer import score_reviews, backfill_lexicon_scores
from pipeline.opinion_scorer import score_opinions
from pipeline.mood_compute import compute_moods, lexicon_check
from pipeline.ai_overview import generate_overview, get_ai_overview

__all__ = [
    "check_unmapped",
    "score_reviews",
    "backfill_lexicon_scores",
    "score_opinions",
    "compute_moods",
    "lexicon_check",
    "generate_overview",
    "get_ai_overview",
]
