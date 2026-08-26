"""Scraper package for Fragrantica perfume data."""

from .scrape import (
    FragranticaScraper,
    scrape_fragrantica,
    scrape_fragrantica_by_brand,
    scrape_fragrantica_brands,
    scrape_fragrantica_by_url,
    scrape_fragrantica_reviews,
)

__all__ = [
    'FragranticaScraper',
    'scrape_fragrantica',
    'scrape_fragrantica_by_brand',
    'scrape_fragrantica_brands',
    'scrape_fragrantica_by_url',
    'scrape_fragrantica_reviews',
]
