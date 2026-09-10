"""Scraper package for Fragrantica perfume data."""

from typing import Any

_EXPORTS = (
    "FragranticaScraper",
    "scrape_fragrantica",
    "scrape_fragrantica_by_brand",
    "scrape_fragrantica_brands",
    "scrape_fragrantica_by_url",
    "scrape_fragrantica_reviews",
)

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        from . import scrape

        return getattr(scrape, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
