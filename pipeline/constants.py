"""Pipeline constants and env-backed config."""

from __future__ import annotations

import os

MOOD_AXES = (
    "happiness",
    "sensuality",
    "energy",
    "soothing",
    "edibility",
    "dominance",
)

# LLM-only mood axes (no NRC-VAD coverage)
LLM_ONLY_MOOD_AXES = (
    "happiness",
    "sensuality",
    "energy",
    "soothing",
    "edibility",
)

# Axes/gates that blend LLM + lexicon when both exist
LEXICON_BLEND_KEYS = ("valence", "dominance")

GATE_KEYS = ("disgust", "valence")

ALL_SCORE_KEYS = MOOD_AXES + GATE_KEYS

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:20128/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "auto")

REVIEW_BLEND_THRESHOLD = int(os.getenv("PIPELINE_REVIEW_BLEND_THRESHOLD", "20"))
DISGUST_GATE = float(os.getenv("PIPELINE_DISGUST_GATE", "40"))
VALENCE_FLOOR = float(os.getenv("PIPELINE_VALENCE_FLOOR", "40"))

LEXICON_PATH = os.getenv("LEXICON_PATH", "data/NRC-VAD-Lexicon-v2.1.txt")
LEXICON_SOURCE = os.getenv("LEXICON_SOURCE", "NRC-VAD-v2.1")
LEXICON_MIN_MATCHED_WORDS = int(os.getenv("LEXICON_MIN_MATCHED_WORDS", "5"))
LEXICON_BLEND_WEIGHT_LLM = float(os.getenv("LEXICON_BLEND_WEIGHT_LLM", "0.65"))
LEXICON_DIVERGENCE_THRESHOLD = float(
    os.getenv("PIPELINE_LEXICON_DIVERGENCE_THRESHOLD", "25")
)

# Community helpfulness weight for review posteriors (vote_yes − vote_no)
REVIEW_HELPFULNESS_WEIGHT_CAP = float(os.getenv("REVIEW_HELPFULNESS_WEIGHT_CAP", "5"))

# Pros/cons opinion scoring
LEXICON_MIN_MATCHED_WORDS_OPINIONS = int(
    os.getenv("LEXICON_MIN_MATCHED_WORDS_OPINIONS", "3")
)
PROS_CONS_WEIGHT_CAP = float(os.getenv("PROS_CONS_WEIGHT_CAP", "8"))

DEFAULT_SCORE_LIMIT = 25
MAX_SCORE_LIMIT = 100
LEXICON_UPSERT_BATCH = 500
