"""
Decrypt Fragrantica inline CryptoJS payloads (status, similar_perfumes, ai_opinions, ...).

Fragrantica embeds vote charts as empty Vue tags in SSR HTML. The real numbers
live in `let status = {"ct","iv","s"}` encrypted with CryptoJS OpenSSL-compatible
AES-256-CBC (EvpKDF-MD5). Browser code decrypts via window._pd; we mirror that.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# Build-constant passphrase used by Fragrantica's inline blob encryptor.
# If decrypt starts failing after a site rebuild, this likely rotated.
INLINE_PASSPHRASE = b"998ed1c8a43d1d2a9c4fd64963db30e4"

SENTIMENT_MAP = {
    "5": "love",
    "4": "like",
    "3": "ok",
    "2": "dislike",
    "1": "hate",
}

LONGEVITY_LABELS = {
    "1": "very_weak",
    "2": "weak",
    "3": "moderate",
    "4": "long_lasting",
    "5": "eternal",
}

SILLAGE_LABELS = {
    "1": "intimate",
    "2": "moderate",
    "3": "strong",
    "4": "enormous",
}

PRICE_VALUE_LABELS = {
    "1": "way_overpriced",
    "2": "overpriced",
    "3": "ok",
    "4": "good_value",
    "5": "great_value",
}

WHEN_TO_WEAR_KEYS = ("winter", "spring", "summer", "fall", "day", "night")
BASE_URL = "https://www.fragrantica.com"


def evp_bytes_to_key(password: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16):
    """OpenSSL / CryptoJS EvpKDF using MD5."""
    derived = b""
    block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len : key_len + iv_len]


def decrypt_cryptojs_blob(blob: Dict[str, str], passphrase: bytes = INLINE_PASSPHRASE) -> Any:
    """Decrypt a CryptoJS {ct, iv, s} blob to a Python object (usually dict/list/bool)."""
    if not isinstance(blob, dict) or not all(k in blob for k in ("ct", "s")):
        raise ValueError("Not a CryptoJS blob")

    salt = bytes.fromhex(blob["s"])
    key, iv = evp_bytes_to_key(passphrase, salt, 32, 16)
    ciphertext = base64.b64decode(blob["ct"])
    plaintext = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext), AES.block_size)
    return json.loads(plaintext.decode("utf-8"))


def extract_inline_blob(html: str, name: str) -> Optional[Dict[str, str]]:
    """Extract `let <name> = {...};` JSON object from page HTML."""
    pattern = rf"(?:let|var|const)\s+{re.escape(name)}\s*=\s*(\{{)"
    match = re.search(pattern, html)
    if not match:
        return None

    start = match.start(1)
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = html[start : i + 1]
                return json.loads(raw)
    return None


def decrypt_inline_var(html: str, name: str) -> Optional[Any]:
    blob = extract_inline_blob(html, name)
    if not blob:
        return None
    try:
        return decrypt_cryptojs_blob(blob)
    except Exception:
        return None


def _pct(votes: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return round((votes / total) * 100.0, 4)


def _histogram(
    votes_map: Optional[Dict[str, Any]],
    labels: Dict[str, str],
    total: int,
) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
    if not votes_map:
        return None
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for key, label in labels.items():
        votes = int(votes_map.get(key) or 0)
        out[label] = {"votes": votes, "percent": _pct(votes, total)}
    return out


def status_to_rating_fields(status_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map decrypted status.status into scraper fields.

    Includes sentiment, when-to-wear, longevity/sillage averages + histograms,
    price value, gender votes, ownership, and fragrantica_id.
    """
    status = status_payload.get("status") or {}
    out: Dict[str, Any] = {
        "fragrantica_id": None,
        "rating_breakdown": None,
        "when_to_wear": None,
        "longevity": None,
        "sillage": None,
        "longevity_breakdown": None,
        "sillage_breakdown": None,
        "price_value": None,
        "gender_votes": None,
        "ownership": None,
        "rating": None,
        "votes": None,
    }

    if status.get("perfume_id") is not None:
        try:
            out["fragrantica_id"] = int(status["perfume_id"])
        except (TypeError, ValueError):
            pass

    rating_votes = status.get("rating") or {}
    rating_sum = int(status.get("rating_sum") or 0)
    if rating_votes:
        breakdown = {}
        for key, label in SENTIMENT_MAP.items():
            votes = int(rating_votes.get(key) or 0)
            breakdown[label] = {
                "votes": votes,
                "percent": _pct(votes, rating_sum),
            }
        out["rating_breakdown"] = breakdown

    if status.get("rating_average") is not None:
        out["rating"] = round(float(status["rating_average"]), 2)
    if rating_sum:
        out["votes"] = rating_sum
    elif status.get("people") is not None:
        out["votes"] = int(status["people"])

    season_raw = {
        "winter": int(status.get("winter") or 0),
        "spring": int(status.get("spring") or 0),
        "summer": int(status.get("summer") or 0),
        "fall": int(status.get("autumn") or status.get("fall") or 0),
        "day": int(status.get("day") or 0),
        "night": int(status.get("night") or 0),
    }
    if any(season_raw.values()):
        season_max = max(season_raw.values())
        out["when_to_wear"] = {
            key: {
                "votes": season_raw[key],
                "percent": _pct(season_raw[key], season_max),
            }
            for key in WHEN_TO_WEAR_KEYS
        }

    longevity_sum = int(status.get("longevity_sum") or 0)
    out["longevity_breakdown"] = _histogram(
        status.get("longevity"), LONGEVITY_LABELS, longevity_sum
    )
    if status.get("longevity_average") is not None:
        out["longevity"] = round((float(status["longevity_average"]) / 5.0) * 10.0, 1)

    sillage_sum = int(status.get("sillage_sum") or 0)
    out["sillage_breakdown"] = _histogram(
        status.get("sillage"), SILLAGE_LABELS, sillage_sum
    )
    if status.get("sillage_average") is not None:
        out["sillage"] = round((float(status["sillage_average"]) / 4.0) * 10.0, 1)

    price_votes = status.get("price_value") or {}
    price_sum = int(status.get("price_value_sum") or 0)
    if price_votes:
        out["price_value"] = {
            "average": round(float(status["price_value_average"]), 4)
            if status.get("price_value_average") is not None
            else None,
            "sum": price_sum or None,
            "breakdown": _histogram(price_votes, PRICE_VALUE_LABELS, price_sum),
        }

    gender_votes = status.get("gender") or {}
    gender_sum = int(status.get("gender_sum") or 0)
    if gender_votes:
        out["gender_votes"] = {
            key: {
                "votes": int(gender_votes.get(key) or 0),
                "percent": _pct(int(gender_votes.get(key) or 0), gender_sum),
            }
            for key in ("female", "female_unisex", "unisex", "male_unisex", "male")
            if key in gender_votes
        }

    relation = status.get("relation") or {}
    relation_sum = int(status.get("relation_sum") or 0)
    if relation:
        out["ownership"] = {
            key: {
                "votes": int(relation.get(key) or 0),
                "percent": _pct(int(relation.get(key) or 0), relation_sum),
            }
            for key in ("have", "had", "want")
            if key in relation
        }

    return out


def normalize_ai_opinions(payload: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize decrypted ai_opinions into pros/cons lists."""
    result = {"pros": [], "cons": []}
    if not payload or not isinstance(payload, dict):
        return result

    for kind in ("pros", "cons"):
        items = payload.get(kind) or []
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            opinion = item.get("opinion") or item.get("en")
            if not opinion:
                continue
            cleaned.append(
                {
                    "opinion": str(opinion).strip(),
                    "vote_yes": int(item.get("vote_yes") or 0),
                    "vote_no": int(item.get("vote_no") or 0),
                    "score": int(item.get("score") or 0),
                }
            )
        result[kind] = cleaned
    return result


def normalize_similar_perfumes(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize decrypted similar_perfumes payload."""
    if not payload or not isinstance(payload, dict):
        return []

    out: List[Dict[str, Any]] = []
    for item in payload.get("similar_perfumes") or []:
        if not isinstance(item, dict):
            continue
        perfume = item.get("perfume") or {}
        rel = perfume.get("perfume_url") or ""
        absolute = urljoin(BASE_URL, rel) if rel else None
        out.append(
            {
                "fragrantica_id": perfume.get("id") or item.get("similar_id"),
                "name": perfume.get("naslov"),
                "brand": perfume.get("dizajner"),
                "gender": perfume.get("spol"),
                "perfume_url": absolute,
                "image_url": perfume.get("thumbnail"),
                "votes": int(item.get("votes") or 0),
                "vote_yes": int(item.get("vote_yes") or 0),
                "vote_no": int(item.get("vote_no") or 0),
            }
        )
    return out


def _strip_html(html: Optional[str]) -> str:
    if not html:
        return ""
    # Lightweight strip without requiring BeautifulSoup in this module.
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def normalize_review_item(
    item: Dict[str, Any],
    *,
    perfume_uuid: str,
    sentiment: str,
) -> Optional[Dict[str, Any]]:
    """Map one Fragrantica review row into a DB-ready dict."""
    if not isinstance(item, dict) or item.get("id") is None:
        return None

    content_html = item.get("komentar") or ""
    member_rel = item.get("member_url") or ""
    member_url = urljoin(BASE_URL, member_rel) if member_rel else None
    avatar = item.get("member_avatar") or item.get("avatar")
    avatar_url = urljoin(BASE_URL, avatar) if avatar and str(avatar).startswith("/") else avatar

    review_date = None
    vrijeme = item.get("vrijeme")
    if vrijeme is not None:
        try:
            from datetime import datetime, timezone

            review_date = datetime.fromtimestamp(int(vrijeme), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            review_date = None

    user_id = item.get("user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        user_id = None

    return {
        "perfume_id": perfume_uuid,
        "fragrantica_review_id": int(item["id"]),
        "fragrantica_perfume_id": item.get("parfem_id"),
        "sentiment": sentiment,
        "username": item.get("username") or item.get("user"),
        "user_id": user_id,
        "content_html": content_html,
        "content_text": _strip_html(content_html),
        "vote_yes": int(item.get("vote_yes") or 0),
        "vote_no": int(item.get("vote_no") or 0),
        "karma_score": float(item["karma_score"]) if item.get("karma_score") is not None else None,
        "review_date": review_date,
        "perfume_votes": item.get("perfume_votes") if isinstance(item.get("perfume_votes"), dict) else None,
        "member_url": member_url,
        "avatar_url": avatar_url,
    }


def normalize_reviews_payload(
    payload: Optional[Dict[str, Any]],
    *,
    perfume_uuid: str,
    sentiment: str,
) -> List[Dict[str, Any]]:
    """Normalize decrypted reviews4perfume_v2 payload into DB rows."""
    if not payload or not isinstance(payload, dict):
        return []

    out: List[Dict[str, Any]] = []
    for item in payload.get("reviews") or []:
        row = normalize_review_item(item, perfume_uuid=perfume_uuid, sentiment=sentiment)
        if row:
            out.append(row)
    return out
