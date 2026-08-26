#!/usr/bin/env python3
"""One-shot loader: NRC-VAD unigrams → emotion_lexicon."""

from __future__ import annotations

import argparse
import os
import sys

# Repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=True)

from pipeline.constants import LEXICON_PATH, LEXICON_SOURCE
from pipeline.lexicon_scorer import load_lexicon


def main() -> int:
    parser = argparse.ArgumentParser(description="Load NRC-VAD lexicon into emotion_lexicon")
    parser.add_argument("--path", default=LEXICON_PATH, help="Path to NRC-VAD TSV")
    parser.add_argument("--source", default=LEXICON_SOURCE, help="Source tag for rows")
    args = parser.parse_args()

    print(f"Loading lexicon from {args.path} (source={args.source}) …")
    n = load_lexicon(path=args.path, source=args.source)
    print(f"Done. Upserted {n} unigram row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
