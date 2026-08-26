-- Migration 006: Allow 0–100 VAD scores in emotion_lexicon
-- Raw NRC-VAD is -1..+1; app stores (raw+1)*50 → up to 100.00
-- Previous numeric(4,2) maxes out at 99.99

ALTER TABLE emotion_lexicon
    ALTER COLUMN valence TYPE numeric(5,2),
    ALTER COLUMN arousal TYPE numeric(5,2),
    ALTER COLUMN dominance TYPE numeric(5,2);

COMMENT ON COLUMN emotion_lexicon.valence IS
    'NRC-VAD valence rescaled to 0–100 via (raw+1)*50';
COMMENT ON COLUMN emotion_lexicon.arousal IS
    'NRC-VAD arousal rescaled to 0–100 (stored; unused in mood blend)';
COMMENT ON COLUMN emotion_lexicon.dominance IS
    'NRC-VAD dominance rescaled to 0–100 via (raw+1)*50';
