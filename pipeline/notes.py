"""Find unmapped accord/note raw texts vs note_aliases."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from utils.db import supabase


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _load_alias_set() -> Set[str]:
    response = supabase.table("note_aliases").select("raw_text").execute()
    return {_norm(row["raw_text"]) for row in (response.data or []) if row.get("raw_text")}


def _collect_raw_from_perfume(perfume: Dict[str, Any]) -> Set[str]:
    raw: Set[str] = set()
    breakdown = perfume.get("accord_breakdown") or {}
    if isinstance(breakdown, dict):
        for key in breakdown.keys():
            n = _norm(str(key))
            if n:
                raw.add(n)
    for field in ("notes_top", "notes_middle", "notes_base"):
        for item in perfume.get(field) or []:
            n = _norm(str(item))
            if n:
                raw.add(n)
    return raw


def check_unmapped(perfume_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Return unmapped raw strings across all (or one) perfume(s).
    """
    query = supabase.table("perfumes").select(
        "id,name,accord_breakdown,notes_top,notes_middle,notes_base"
    )
    if perfume_id:
        query = query.eq("id", perfume_id)
    response = query.execute()
    perfumes = response.data or []

    aliases = _load_alias_set()
    unmapped: Set[str] = set()
    perfume_hits: Dict[str, List[str]] = {}

    for perfume in perfumes:
        raws = _collect_raw_from_perfume(perfume)
        missing = sorted(r for r in raws if r not in aliases)
        if missing:
            perfume_hits[perfume["id"]] = missing
            unmapped.update(missing)

    return {
        "unmapped": sorted(unmapped),
        "count": len(unmapped),
        "perfume_count_checked": len(perfumes),
        "perfume_ids_affected": sorted(perfume_hits.keys()),
        "by_perfume": perfume_hits,
    }
