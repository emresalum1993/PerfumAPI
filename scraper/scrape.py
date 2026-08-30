"""
Fragrantica perfume scraper.
Extracts perfume data from Fragrantica.com for educational purposes.
"""

import requests
from bs4 import BeautifulSoup
import time
import json
import os
import sys
from typing import List, Dict, Any, Optional, Tuple
import re
import random
from urllib.parse import parse_qsl, urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.fragrantica_crypto import (
    decrypt_inline_var,
    decrypt_cryptojs_blob,
    status_to_rating_fields,
    normalize_ai_opinions,
    normalize_similar_perfumes,
    normalize_reviews_payload,
)


# Known category keys on Fragrantica tw-rating-card sections (DOM fallback)
SENTIMENT_KEYS = ('love', 'like', 'ok', 'dislike', 'hate')
WHEN_TO_WEAR_KEYS = ('winter', 'spring', 'summer', 'fall', 'day', 'night')


class FragranticaScraper:
    """
    Scraper for Fragrantica.com perfume database.
    Respects rate limiting and handles errors gracefully.
    """
    
    def __init__(self, delay: float = 17.0):
        """
        Initialize the scraper.
        
        Args:
            delay: Minimum delay between requests in seconds (default 17.0)
        """
        self.delay = delay
        self.max_retries = 5  # Retry up to 5 times on 403/429/network errors
        self.retry_delay = 15  # Base retry delay for errors
        self.request_count = 0  # Track requests for progressive slowdown
        self.last_url = None  # Track last URL for referer
        self.last_html = None  # Raw HTML of last successful page fetch
        
        # Diverse User-Agent strings to rotate through
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        self.base_url = "https://www.fragrantica.com"
        self._reset_session()

    def _reset_session(self):
        """Create a fresh requests session with a random User-Agent."""
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.last_url or self.base_url,
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Cache-Control': 'max-age=0'
        })
    
    def _get_page(self, url: str) -> Optional[BeautifulSoup]:
        """
        Fetch and parse a web page with robust retry logic for 403 / 429 rate limiting.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                # Rotate User-Agent to appear like different users
                self.session.headers['User-Agent'] = random.choice(self.user_agents)
                if self.last_url:
                    self.session.headers['Referer'] = self.last_url
                else:
                    self.session.headers['Referer'] = self.base_url
                
                print(f"📡 Fetching: {url} (attempt {attempt}/{self.max_retries})")
                response = self.session.get(url, timeout=20)
                
                # Handle rate limiting or Cloudflare blocks (429 / 403)
                if response.status_code in (403, 429):
                    wait = min(45, 10 * attempt)
                    print(f"⚠️  Received HTTP {response.status_code} for {url}. Resetting session & waiting {wait}s (attempt {attempt}/{self.max_retries})...")
                    time.sleep(wait)
                    self._reset_session()
                    continue
                
                response.raise_for_status()
                
                # Update last URL for next referer
                self.last_url = url
                self.request_count += 1
                
                # Random pause every 5-10 requests
                if self.request_count % random.randint(5, 10) == 0:
                    reading_pause = random.uniform(15, 30)
                    print(f"📖 Reading pause for {reading_pause:.1f}s...")
                    time.sleep(reading_pause)
                
                # Progressive slowdown
                progressive_delay = (self.request_count // 20) * 2
                base_delay = self.delay + progressive_delay
                actual_delay = max(5.0, base_delay + random.uniform(-base_delay * 0.3, base_delay * 0.3))
                
                print(f"⏳ Waiting {actual_delay:.1f} seconds...")
                time.sleep(actual_delay)
                
                self.last_html = response.text
                return BeautifulSoup(response.text, 'html.parser')
                
            except Exception as e:
                wait = min(45, 10 * attempt)
                print(f"❌ Error fetching {url}: {e}. Resetting session & waiting {wait}s (attempt {attempt}/{self.max_retries})...")
                time.sleep(wait)
                self._reset_session()
                
        print(f"❌ Max retries ({self.max_retries}) reached for {url}")
        self.last_html = None
        return None
        self.last_html = None
        return None
    
    def _extract_designer_id(self, brand_url: str) -> Optional[int]:
        """
        Extract designer ID from brand page HTML.
        
        Args:
            brand_url: URL of the brand page
            
        Returns:
            Designer ID as integer or None if not found
        """
        soup = self._get_page(brand_url)
        if not soup:
            return None
        
        # Method 1: Extract from og:image meta tag (most reliable)
        # Pattern: <meta property="og:image" content="https://www.fragrantica.com/mdimg/dizajneri/o.720.jpg" />
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            content = og_image.get('content')
            # Extract ID from pattern: o.{ID}.jpg
            match = re.search(r'/o\.(\d+)\.jpg', content)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, TypeError):
                    pass
        
        # Method 2: Check for data-designer-id attribute
        designer_elem = soup.find(attrs={'data-designer-id': True})
        if designer_elem:
            try:
                return int(designer_elem.get('data-designer-id'))
            except (ValueError, TypeError):
                pass
        
        # Method 3: Look in JavaScript/script tags for designerId or designer_id
        scripts = soup.find_all('script')
        for script in scripts:
            script_text = script.string
            if script_text:
                # Pattern: designer_id = 123 or designerId: 123 or "designer_id":"123"
                matches = re.findall(r'designer[_-]?id["\']?\s*[:=]\s*["\']?(\d+)', script_text, re.IGNORECASE)
                if matches:
                    try:
                        return int(matches[0])
                    except (ValueError, TypeError):
                        pass
        
        # Method 4: Extract from URL patterns in page (e.g., AJAX calls)
        # Look for links or data containing designer ID
        all_text = soup.get_text()
        matches = re.findall(r'designer_id[=:](\d+)', all_text)
        if matches:
            try:
                return int(matches[0])
            except (ValueError, TypeError):
                pass
        
        print(f"⚠️  Could not extract designer ID from {brand_url}")
        return None
    
    def get_popular_perfumes_urls(self, limit: int = 1000) -> List[str]:
        """
        Get URLs of popular perfumes from search page.
        
        Args:
            limit: Maximum number of perfume URLs to retrieve
            
        Returns:
            List of perfume URLs
        """
        perfume_urls = []
        page = 1
        
        print(f"🔍 Searching for up to {limit} popular perfumes...")
        
        while len(perfume_urls) < limit:
            # Fragrantica search URL for most popular perfumes
            search_url = f"{self.base_url}/search/"
            
            if page > 1:
                search_url = f"{self.base_url}/search/?page={page}"
            
            soup = self._get_page(search_url)
            if not soup:
                break
            
            # Find all perfume links with pattern /perfume/Brand/Name-ID.html
            # This pattern matches actual perfume detail pages
            perfume_links = soup.find_all('a', href=re.compile(r'/perfume/[^/]+/[^/]+\.html'))
            
            if not perfume_links:
                print(f"⚠️  No more perfumes found on page {page}")
                break
            
            found_on_page = 0
            for link in perfume_links:
                perfume_url = link.get('href', '')
                
                # Skip if not a valid perfume URL
                if not perfume_url or '/perfume/' not in perfume_url:
                    continue
                
                # Convert to full URL if needed
                if not perfume_url.startswith('http'):
                    perfume_url = self.base_url + perfume_url
                
                # Add if not already in list
                if perfume_url not in perfume_urls:
                    perfume_urls.append(perfume_url)
                    found_on_page += 1
                    print(f"  Found perfume {len(perfume_urls)}: {perfume_url}")
                    
                    if len(perfume_urls) >= limit:
                        break
            
            # If no new perfumes found on this page, stop
            if found_on_page == 0:
                print(f"⚠️  No new perfumes found on page {page}")
                break
            
            page += 1
            
            # Safety limit: don't scrape more than 50 pages
            if page > 50:
                print("⚠️  Reached page limit (50)")
                break
        
        print(f"✅ Found {len(perfume_urls)} perfume URLs")
        return perfume_urls[:limit]
    
    def get_brand_perfumes_urls(self, brand_name: str, limit: int = 100) -> List[str]:
        """
        Get URLs of perfumes from a specific brand, sorted by popularity.
        
        Args:
            brand_name: Name of the brand (e.g., "Jean Paul Gaultier", "Xerjoff")
            limit: Maximum number of perfume URLs to retrieve
            
        Returns:
            List of perfume URLs for the specified brand, sorted by popularity
        """
        perfume_urls = []
        
        # Format brand name for URL: replace spaces with hyphens, keep other characters
        # Examples: "Jean Paul Gaultier" -> "Jean-Paul-Gaultier"
        #           "Xerjoff" -> "Xerjoff"
        formatted_brand = brand_name.replace(' ', '-')
        
        print(f"🔍 Searching for popular perfumes by {brand_name} (up to {limit})...")
        
        # First, get the designer ID from the brand page
        brand_url = f"{self.base_url}/designers/{formatted_brand}.html"
        designer_id = self._extract_designer_id(brand_url)
        
        if not designer_id:
            print(f"❌ Could not find designer ID for {brand_name}")
            return []
        
        print(f"✓ Found designer ID: {designer_id}")
        
        # Use AJAX endpoint to get perfumes sorted by popularity
        ajax_url = f"{self.base_url}/ajax.php?designerSimpleList"
        
        # Rotate User-Agent for AJAX request
        self.session.headers['User-Agent'] = random.choice(self.user_agents)
        
        # Update referer to brand page (simulate clicking from that page)
        self.session.headers['Referer'] = brand_url
        
        try:
            print(f"📡 Fetching popular perfumes via AJAX...")
            
            # POST request with form data
            form_data = f"action=simple.perfume.list&designer_id={designer_id}&mode=popular"
            
            response = self.session.post(
                ajax_url,
                data=form_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                print(f"⚠️  Rate limited (429). Waiting {self.retry_delay} seconds...")
                time.sleep(self.retry_delay)
                return []
            
            response.raise_for_status()
            
            # Update last URL for next referer
            self.last_url = brand_url
            
            # Increment request counter
            self.request_count += 1
            
            # Parse the HTML response
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find all perfume links with pattern /perfume/Brand/Name-ID.html
            perfume_links = soup.find_all('a', href=re.compile(r'/perfume/[^/]+/[^/]+\.html'))
            
            if not perfume_links:
                print(f"⚠️  No perfumes found in AJAX response")
                return []
            
            # Extract perfume URLs
            for link in perfume_links:
                perfume_url = link.get('href', '')
                
                # Skip if not a valid perfume URL
                if not perfume_url or '/perfume/' not in perfume_url:
                    continue
                
                # Convert to full URL if needed
                if not perfume_url.startswith('http'):
                    perfume_url = self.base_url + perfume_url
                
                # Add if not already in list
                if perfume_url not in perfume_urls:
                    perfume_urls.append(perfume_url)
                    print(f"  Found perfume {len(perfume_urls)}: {perfume_url}")
                    
                    if len(perfume_urls) >= limit:
                        break
            
            # Add delay after AJAX request (same as _get_page logic)
            progressive_delay = (self.request_count // 20) * 2
            base_delay = self.delay + progressive_delay
            actual_delay = base_delay + random.uniform(-base_delay * 0.3, base_delay * 0.3)
            actual_delay = max(10.0, actual_delay)
            
            print(f"⏳ Waiting {actual_delay:.1f} seconds...")
            time.sleep(actual_delay)
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching from AJAX endpoint: {str(e)}")
            return []
        except Exception as e:
            print(f"❌ Unexpected error: {str(e)}")
            return []
        
        print(f"✅ Found {len(perfume_urls)} popular perfume URLs for {brand_name}")
        return perfume_urls[:limit]

    @staticmethod
    def _parse_vote_count(text: str) -> Optional[int]:
        """Parse Fragrantica vote displays like '12.8k', '3k', '6500' into integers."""
        if not text:
            return None
        cleaned = text.strip().lower().replace(',', '').replace('\u00a0', '')
        match = re.match(r'^([\d.]+)\s*([km])?$', cleaned)
        if not match:
            return None
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        suffix = match.group(2)
        if suffix == 'k':
            value *= 1000
        elif suffix == 'm':
            value *= 1_000_000
        return int(round(value))

    @staticmethod
    def _parse_percent_from_style(style: Optional[str]) -> Optional[float]:
        """Extract percent from inline style width: 43.4549%;"""
        if not style:
            return None
        match = re.search(r'width:\s*([\d.]+)\s*%', style, re.IGNORECASE)
        if not match:
            return None
        try:
            return round(float(match.group(1)), 4)
        except ValueError:
            return None

    def _extract_card_category_entries(
        self,
        card,
        allowed_keys: Tuple[str, ...],
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Deterministically parse one .tw-rating-card into {key: {votes, percent}}.
        Keys come from category label spans (love, winter, etc.), not SVG icons.
        """
        entries: Dict[str, Dict[str, Optional[float]]] = {}
        allowed = set(allowed_keys)

        for span in card.find_all('span'):
            key = span.get_text(strip=True).lower()
            if key not in allowed or key in entries:
                continue

            column = span.parent
            if column is None:
                continue

            percent = None
            bar = column.find('div', style=re.compile(r'width:\s*[\d.]+%', re.I))
            if bar:
                percent = self._parse_percent_from_style(bar.get('style'))

            votes = None
            for candidate in column.find_all('span'):
                if candidate is span:
                    continue
                parsed = self._parse_vote_count(candidate.get_text(strip=True))
                if parsed is not None:
                    votes = parsed

            entries[key] = {
                'votes': votes,
                'percent': percent,
            }

        # Keep stable key order for known categories
        return {k: entries[k] for k in allowed_keys if k in entries}

    def _extract_tw_rating_cards(self, soup: BeautifulSoup) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Extract sentiment (Rating) and season/time (When To Wear) cards.
        Returns (rating_breakdown, when_to_wear).
        """
        rating_breakdown: Dict[str, Any] = {}
        when_to_wear: Dict[str, Any] = {}

        for card in soup.find_all(class_='tw-rating-card'):
            label_el = card.find(class_='tw-rating-card-label')
            if not label_el:
                continue
            label = label_el.get_text(strip=True).lower()

            if label == 'rating':
                rating_breakdown = self._extract_card_category_entries(card, SENTIMENT_KEYS)
            elif 'when to wear' in label or label == 'when to wear':
                when_to_wear = self._extract_card_category_entries(card, WHEN_TO_WEAR_KEYS)

        return rating_breakdown, when_to_wear

    def _extract_main_accords(self, soup: BeautifulSoup) -> List[str]:
        """Extract main accords from SSR page text near the Main accords heading."""
        texts: List[str] = []
        heading_idx = None
        # Prefer a local container around the heading
        for el in soup.find_all(string=re.compile(r'^\s*main accords\s*$', re.I)):
            container = el.find_parent('div')
            for _ in range(3):
                if container and container.parent and container.parent.name == 'div':
                    container = container.parent
            if container:
                texts = [t.strip() for t in container.stripped_strings if t.strip()]
            break

        if not texts:
            return []

        for i, t in enumerate(texts):
            if t.lower() == 'main accords':
                heading_idx = i
                break
        if heading_idx is None:
            return []

        stop_prefixes = (
            'search by accords',
            'many items for sale',
            'sponsored',
            'user ratings',
            'rating',
            'when to wear',
            'perfume rating',
            'reviews',
        )
        accords: List[str] = []
        for t in texts[heading_idx + 1:]:
            lower = t.lower()
            if any(lower.startswith(p) for p in stop_prefixes):
                break
            if re.fullmatch(r'[\d.,kKmM]+', t):
                continue
            if len(t) < 2 or len(t) > 30:
                continue
            if len(t.split()) > 3:
                continue
            if t not in accords:
                accords.append(t)
            if len(accords) >= 12:
                break
        return accords

    def _extract_accord_breakdown(self, soup: BeautifulSoup) -> Dict[str, int]:
        """Parse accord percentages from the 'Search by accords' link query string."""
        link = soup.find("a", href=re.compile(r"/accords-search/\?"), string=re.compile(r"search by accords", re.I))
        if not link:
            link = soup.find("a", href=re.compile(r"/accords-search/\?"))
        if not link or not link.get("href"):
            return {}

        parsed = urlparse(link["href"])
        breakdown: Dict[str, int] = {}
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            if key == "f_from_perfume_id":
                continue
            try:
                percent = int(value)
            except (TypeError, ValueError):
                continue
            accord = key.replace("+", " ").strip().lower()
            if accord:
                breakdown[accord] = percent
        return breakdown

    def _extract_perfumer(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract perfumer/nose from /noses/ links."""
        for a in soup.find_all('a', href=re.compile(r'/noses/[^"]+\.html')):
            name = a.get_text(strip=True)
            if name and name.lower() not in {'perfumers', 'noses', 'perfumer'}:
                return name
        return None

    def _extract_og_image(self, soup: BeautifulSoup, fragrantica_id: Optional[int] = None) -> Optional[str]:
        """Prefer full bottle / social OG image over thumbnail."""
        og_images = []
        for tag in soup.find_all('meta', property=re.compile(r'^og:image')):
            content = tag.get('content')
            if content:
                og_images.append(content)

        for content in og_images:
            if '/perfume/o.' in content:
                return content
        for content in og_images:
            if 'perfume-social' in content or '/perfume/social.' in content:
                return content
        if fragrantica_id:
            return f'https://fimgs.net/mdimg/perfume/o.{fragrantica_id}.jpg'
        return og_images[0] if og_images else None
    
    def extract_perfume_details(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract detailed information from a perfume page.
        
        Args:
            url: Perfume detail page URL
            
        Returns:
            Dictionary with perfume data or None if failed
        """
        soup = self._get_page(url)
        if not soup:
            return None
        
        try:
            perfume_data = {
                'perfume_url': url,
                'name': None,
                'brand': None,
                'release_year': None,
                'gender': None,
                'fragrantica_id': None,
                'fragrance_family': None,
                'perfumer': None,
                'main_accords': [],
                'accord_breakdown': None,
                'notes_top': [],
                'notes_middle': [],
                'notes_base': [],
                'rating': None,
                'votes': None,
                'rating_breakdown': None,
                'when_to_wear': None,
                'longevity_breakdown': None,
                'sillage_breakdown': None,
                'price_value': None,
                'gender_votes': None,
                'ownership': None,
                'pros': None,
                'cons': None,
                'similar_perfumes': None,
                'description': None,
                'longevity': None,
                'sillage': None,
                'image_url': None,
                'image_url_og': None,
            }
            
            # Extract name and gender from h1 title
            name_tag = soup.find('h1', itemprop='name')
            if name_tag:
                full_title = name_tag.get_text(strip=True)
                
                # Extract gender from title
                if 'for women and men' in full_title.lower():
                    perfume_data['gender'] = 'Unisex'
                    clean_name = re.sub(r'\s*for women and men\s*', '', full_title, flags=re.IGNORECASE)
                elif 'for women' in full_title.lower():
                    perfume_data['gender'] = 'Women'
                    clean_name = re.sub(r'\s*for women\s*', '', full_title, flags=re.IGNORECASE)
                elif 'for men' in full_title.lower():
                    perfume_data['gender'] = 'Men'
                    clean_name = re.sub(r'\s*for men\s*', '', full_title, flags=re.IGNORECASE)
                elif 'for unisex' in full_title.lower():
                    perfume_data['gender'] = 'Unisex'
                    clean_name = re.sub(r'\s*for unisex\s*', '', full_title, flags=re.IGNORECASE)
                elif 'for her' in full_title.lower():
                    perfume_data['gender'] = 'Women'
                    clean_name = re.sub(r'\s*for her\s*', '', full_title, flags=re.IGNORECASE)
                elif 'for him' in full_title.lower():
                    perfume_data['gender'] = 'Men'
                    clean_name = re.sub(r'\s*for him\s*', '', full_title, flags=re.IGNORECASE)
                else:
                    clean_name = full_title
                
                # Clean up the name (remove extra spaces and fix spacing issues)
                clean_name = re.sub(r'\s+', ' ', clean_name.strip())
                perfume_data['name'] = clean_name
            
            # Extract brand
            brand_tag = soup.find('span', itemprop='name')
            if not brand_tag:
                brand_tag = soup.find('a', class_='brand')
            if brand_tag:
                perfume_data['brand'] = brand_tag.get_text(strip=True)

            # Strip duplicated brand suffix from name when present
            if perfume_data.get('name') and perfume_data.get('brand'):
                brand = perfume_data['brand']
                name = perfume_data['name']
                if name.lower().endswith(brand.lower()):
                    perfume_data['name'] = name[: -len(brand)].strip()
            
            # Extract release year
            # First priority: Search in description for launch patterns (most reliable)
            desc_tag = soup.find('div', itemprop='description')
            if not desc_tag:
                desc_tag = soup.find('div', class_='text-description')
            if desc_tag:
                desc_text = desc_tag.get_text()
                # Look for patterns like "launched in 2020", "released in 2020"
                launch_patterns = [
                    r'launched in (\d{4})',
                    r'released in (\d{4})', 
                    r'was launched in (\d{4})',
                    r'was released in (\d{4})'
                ]
                for pattern in launch_patterns:
                    match = re.search(pattern, desc_text, re.IGNORECASE)
                    if match:
                        year = int(match.group(1))
                        if 1900 <= year <= 2030:  # Reasonable year range
                            perfume_data['release_year'] = year
                            break

                # Fragrance family: "is a Oriental Floral fragrance"
                family_match = re.search(
                    r'is an?\s+(.+?)\s+fragrance\b',
                    desc_text,
                    re.IGNORECASE,
                )
                if family_match:
                    perfume_data['fragrance_family'] = family_match.group(1).strip()

                # Perfumer from description as fallback
                nose_match = re.search(
                    r'The nose behind this fragrance is\s+([^.]+)\.',
                    desc_text,
                    re.IGNORECASE,
                )
                if nose_match:
                    perfume_data['perfumer'] = nose_match.group(1).strip()
            
            # Second priority: Look in the main info section
            if not perfume_data['release_year']:
                main_info = soup.find('div', class_='main-info')
                if main_info:
                    year_match = re.search(r'\b(19|20)\d{2}\b', main_info.get_text())
                    if year_match:
                        year = int(year_match.group())
                        if 1900 <= year <= 2030:
                            perfume_data['release_year'] = year
            
            # Third priority: Look for standalone year div (but exclude vote-box divs)
            if not perfume_data['release_year']:
                # Find all divs with 4-digit text
                all_divs = soup.find_all('div')
                for div in all_divs:
                    if div.string and re.match(r'^(19|20)\d{2}$', div.string.strip()):
                        # Skip divs that are inside vote-box elements
                        parent_classes = ' '.join(div.parent.get('class', [])) if div.parent else ''
                        if 'vote-box' not in parent_classes.lower():
                            try:
                                year = int(div.string.strip())
                                if 1900 <= year <= 2030:
                                    perfume_data['release_year'] = year
                                    break
                            except ValueError:
                                continue
            
            # Gender already extracted from h1 title above, fallback check if not found
            if not perfume_data['gender']:
                gender_tag = soup.find('p', style=re.compile(r'text-align'))
                if gender_tag:
                    gender_text = gender_tag.get_text(strip=True).lower()
                    if 'for women and men' in gender_text or 'unisex' in gender_text or 'for unisex' in gender_text:
                        perfume_data['gender'] = 'Unisex'
                    elif 'for women' in gender_text or 'for her' in gender_text:
                        perfume_data['gender'] = 'Women'
                    elif 'for men' in gender_text or 'for him' in gender_text:
                        perfume_data['gender'] = 'Men'
            
            # Extract notes pyramid using h4 headers (primary method)
            # Top notes
            top_notes_header = soup.find('h4', string=re.compile(r'Top Notes', re.I))
            if top_notes_header:
                # Find the next div after the header that contains the notes
                notes_container = top_notes_header.find_next_sibling('div')
                if notes_container:
                    # Extract note names from links within the container
                    note_links = notes_container.find_all('a', href=re.compile(r'/notes/'))
                    notes = []
                    for link in note_links:
                        note_text = link.get_text(strip=True)
                        if note_text:
                            notes.append(note_text)
                    perfume_data['notes_top'] = notes
            
            # Middle notes
            middle_notes_header = soup.find('h4', string=re.compile(r'Middle Notes', re.I))
            if middle_notes_header:
                notes_container = middle_notes_header.find_next_sibling('div')
                if notes_container:
                    note_links = notes_container.find_all('a', href=re.compile(r'/notes/'))
                    notes = []
                    for link in note_links:
                        note_text = link.get_text(strip=True)
                        if note_text:
                            notes.append(note_text)
                    perfume_data['notes_middle'] = notes
            
            # Base notes
            base_notes_header = soup.find('h4', string=re.compile(r'Base Notes', re.I))
            if base_notes_header:
                notes_container = base_notes_header.find_next_sibling('div')
                if notes_container:
                    note_links = notes_container.find_all('a', href=re.compile(r'/notes/'))
                    notes = []
                    for link in note_links:
                        note_text = link.get_text(strip=True)
                        if note_text:
                            notes.append(note_text)
                    perfume_data['notes_base'] = notes
            
            # Fallback: Extract notes from description text if primary method failed
            if not perfume_data['notes_top'] and not perfume_data['notes_middle'] and not perfume_data['notes_base']:
                desc_tag = soup.find('div', itemprop='description')
                if not desc_tag:
                    desc_tag = soup.find('div', class_='text-description')
                if desc_tag:
                    desc_text = desc_tag.get_text()
                    
                    # Extract top notes
                    top_match = re.search(r'Top notes are ([^;]+)', desc_text, re.IGNORECASE)
                    if top_match:
                        top_notes_text = top_match.group(1)
                        # Split on commas and handle "and" connections
                        notes = re.split(r',|\s+and\s+', top_notes_text)
                        perfume_data['notes_top'] = [note.strip() for note in notes if note.strip()]
                    
                    # Extract middle notes
                    middle_match = re.search(r'middle notes are ([^;]+)', desc_text, re.IGNORECASE)
                    if middle_match:
                        middle_notes_text = middle_match.group(1)
                        notes = re.split(r',|\s+and\s+', middle_notes_text)
                        perfume_data['notes_middle'] = [note.strip() for note in notes if note.strip()]
                        
                    # Extract base notes
                    base_match = re.search(r'base notes are ([^.;]+)', desc_text, re.IGNORECASE)
                    if base_match:
                        base_notes_text = base_match.group(1)
                        notes = re.split(r',|\s+and\s+', base_notes_text)
                        perfume_data['notes_base'] = [note.strip() for note in notes if note.strip()]
            
            # Extract rating and votes
            rating_tag = soup.find('span', itemprop='ratingValue')
            if rating_tag:
                try:
                    perfume_data['rating'] = float(rating_tag.get_text(strip=True))
                except ValueError:
                    pass
            
            votes_tag = soup.find('span', itemprop='ratingCount')
            if votes_tag:
                try:
                    perfume_data['votes'] = int(votes_tag.get_text(strip=True).replace(',', ''))
                except ValueError:
                    pass

            # Primary source for sentiment / when-to-wear / longevity / sillage:
            # encrypted `let status = {ct,iv,s}` blob (Vue cards are empty shells in SSR HTML).
            html = self.last_html or ""
            status_payload = decrypt_inline_var(html, "status")
            if status_payload:
                derived = status_to_rating_fields(status_payload)
                for key in (
                    "fragrantica_id",
                    "rating_breakdown",
                    "when_to_wear",
                    "longevity_breakdown",
                    "sillage_breakdown",
                    "price_value",
                    "gender_votes",
                    "ownership",
                    "rating",
                    "votes",
                    "longevity",
                    "sillage",
                ):
                    if derived.get(key) is not None:
                        perfume_data[key] = derived[key]
            else:
                # DOM fallback if status blob missing / passphrase rotated
                rating_breakdown, when_to_wear = self._extract_tw_rating_cards(soup)
                if rating_breakdown:
                    perfume_data['rating_breakdown'] = rating_breakdown
                if when_to_wear:
                    perfume_data['when_to_wear'] = when_to_wear

            # Pros / cons + similar perfumes (encrypted blobs)
            opinions = normalize_ai_opinions(decrypt_inline_var(html, "ai_opinions"))
            if opinions.get("pros"):
                perfume_data["pros"] = opinions["pros"]
            if opinions.get("cons"):
                perfume_data["cons"] = opinions["cons"]

            similars = normalize_similar_perfumes(decrypt_inline_var(html, "similar_perfumes"))
            if similars:
                perfume_data["similar_perfumes"] = similars

            # Main accords + perfumer from SSR HTML
            accords = self._extract_main_accords(soup)
            if accords:
                perfume_data["main_accords"] = accords
            accord_breakdown = self._extract_accord_breakdown(soup)
            if accord_breakdown:
                perfume_data["accord_breakdown"] = accord_breakdown

            perfumer = self._extract_perfumer(soup)
            if perfumer:
                perfume_data["perfumer"] = perfumer

            # Prefer fragrantica_id from URL if still missing
            if not perfume_data.get("fragrantica_id"):
                id_match = re.search(r'-(\d+)\.html(?:$|\?)', url)
                if id_match:
                    perfume_data["fragrantica_id"] = int(id_match.group(1))
            
            # Extract description with proper formatting
            desc_container = soup.find('div', itemprop='description')
            if not desc_container:
                desc_container = soup.find('div', class_='text-description')
            
            if desc_container:
                description_parts = []
                
                # Extract main description (first <p> tag, excluding reviewstrigger)
                first_p = desc_container.find('p', class_=lambda x: x != 'reviewstrigger')
                if first_p:
                    # Use separator=' ' to add spaces between bold/inline elements
                    main_desc = first_p.get_text(separator=' ', strip=True)
                    if main_desc:
                        description_parts.append(main_desc)
                
                # Extract blockquote content if present
                blockquote = desc_container.find('div', class_='fragrantica-blockquote')
                if blockquote:
                    # Get all <p> tags within blockquote
                    blockquote_paragraphs = blockquote.find_all('p')
                    for p in blockquote_paragraphs:
                        p_text = p.get_text(separator=' ', strip=True)
                        if p_text:
                            description_parts.append(p_text)
                
                # Combine parts with double newline separator
                if description_parts:
                    full_description = '\n\n'.join(description_parts)
                    # Limit total length to prevent extremely long descriptions
                    perfume_data['description'] = full_description[:2000]
            
            # Extract longevity/sillage from blue paragraphs only if status blob didn't provide them
            blue_p_tags = soup.find_all('p', style=re.compile(r'color:\s*#83a6c4'))
            if perfume_data.get('longevity') is None:
                for p_tag in blue_p_tags:
                    p_text = p_tag.get_text()
                    if 'perfume longevity:' in p_text.lower():
                        spans = p_tag.find_all('span')
                        if len(spans) >= 2:
                            try:
                                longevity_value = float(spans[0].get_text(strip=True))
                                max_value = float(spans[1].get_text(strip=True))
                                if max_value > 0:
                                    perfume_data['longevity'] = round((longevity_value / max_value) * 10, 1)
                            except (ValueError, ZeroDivisionError):
                                pass
                        break

            if perfume_data.get('sillage') is None:
                for p_tag in blue_p_tags:
                    p_text = p_tag.get_text()
                    if 'perfume sillage:' in p_text.lower():
                        spans = p_tag.find_all('span')
                        if len(spans) >= 2:
                            try:
                                sillage_value = float(spans[0].get_text(strip=True))
                                max_value = float(spans[1].get_text(strip=True))
                                if max_value > 0:
                                    perfume_data['sillage'] = round((sillage_value / max_value) * 10, 1)
                            except (ValueError, ZeroDivisionError):
                                pass
                        break
            
            # Extract image URL
            img_tag = soup.find('img', itemprop='image')
            if img_tag and 'src' in img_tag.attrs:
                perfume_data['image_url'] = img_tag['src']

            # Higher-quality / social image
            perfume_data['image_url_og'] = self._extract_og_image(soup, perfume_data.get('fragrantica_id'))
            
            print(f"✅ Extracted: {perfume_data.get('name', 'Unknown')} by {perfume_data.get('brand', 'Unknown')}")
            return perfume_data
            
        except Exception as e:
            print(f"❌ Error extracting perfume data from {url}: {str(e)}")
            return None
    
    def scrape_perfumes(self, limit: int = 2, save_to_file: bool = True) -> List[Dict[str, Any]]:
        """
        Main scraping function: get perfume URLs and extract details.
        
        Args:
            limit: Number of perfumes to scrape (default 2 for testing)
            save_to_file: Whether to save results to data.json
            
        Returns:
            List of perfume dictionaries
        """
        print(f"🚀 Starting scrape for {limit} perfumes...")
        
        # Visit homepage first to establish session and get cookies
        print("🌐 Establishing session with Fragrantica...")
        try:
            # Rotate User-Agent for initial session
            self.session.headers['User-Agent'] = random.choice(self.user_agents)
            self.session.get(self.base_url, timeout=15)
            time.sleep(3)  # Brief pause after initial connection
        except Exception as e:
            print(f"⚠️  Warning: Could not establish initial session: {e}")
        
        # Get perfume URLs
        urls = self.get_popular_perfumes_urls(limit)
        
        if not urls:
            print("❌ No perfume URLs found")
            return []
        
        # Extract details for each perfume
        perfumes = []
        for i, url in enumerate(urls, 1):
            print(f"\n📋 Processing perfume {i}/{len(urls)}")
            perfume_data = self.extract_perfume_details(url)
            
            if perfume_data and perfume_data.get('name'):
                perfumes.append(perfume_data)
            else:
                print(f"⚠️  Skipped perfume (incomplete data)")
        
        print(f"\n✅ Successfully scraped {len(perfumes)}/{len(urls)} perfumes")
        
        # Save to file
        if save_to_file and perfumes:
            self.save_to_json(perfumes)
        
        return perfumes
    
    def scrape_by_brand(self, brand_name: str, limit: int = 100, save_to_file: bool = True) -> List[Dict[str, Any]]:
        """
        Scrape perfumes from a specific brand.
        
        Args:
            brand_name: Name of the brand (e.g., "Jean Paul Gaultier")
            limit: Number of perfumes to scrape from this brand (default 100)
            save_to_file: Whether to save results to data.json
            
        Returns:
            List of perfume dictionaries
        """
        print(f"🚀 Starting scrape for {brand_name} (up to {limit} perfumes)...")
        
        # Visit homepage first to establish session and get cookies
        print("🌐 Establishing session with Fragrantica...")
        try:
            # Rotate User-Agent for initial session
            self.session.headers['User-Agent'] = random.choice(self.user_agents)
            self.session.get(self.base_url, timeout=15)
            time.sleep(3)  # Brief pause after initial connection
        except Exception as e:
            print(f"⚠️  Warning: Could not establish initial session: {e}")
        
        # Get perfume URLs for this brand
        urls = self.get_brand_perfumes_urls(brand_name, limit)
        
        if not urls:
            print(f"❌ No perfume URLs found for {brand_name}")
            return []
        
        # Extract details for each perfume
        perfumes = []
        for i, url in enumerate(urls, 1):
            print(f"\n📋 Processing perfume {i}/{len(urls)}")
            perfume_data = self.extract_perfume_details(url)
            
            if perfume_data and perfume_data.get('name'):
                perfumes.append(perfume_data)
            else:
                print(f"⚠️  Skipped perfume (incomplete data)")
        
        print(f"\n✅ Successfully scraped {len(perfumes)}/{len(urls)} perfumes from {brand_name}")
        
        # Save to file
        if save_to_file and perfumes:
            self.save_to_json(perfumes)
        
        return perfumes
    
    def scrape_multiple_brands(self, brands: List[str], limit_per_brand: int = 100, save_to_file: bool = True) -> List[Dict[str, Any]]:
        """
        Scrape perfumes from multiple brands.
        
        Args:
            brands: List of brand names (e.g., ["Jean Paul Gaultier", "Xerjoff", "Creed"])
            limit_per_brand: Number of perfumes to scrape per brand (default 100)
            save_to_file: Whether to save combined results to data.json
            
        Returns:
            Combined list of perfume dictionaries from all brands
        """
        print(f"🚀 Starting multi-brand scrape for {len(brands)} brands...")
        print(f"📋 Brands: {', '.join(brands)}")
        
        all_perfumes = []
        
        for i, brand_name in enumerate(brands, 1):
            print(f"\n{'='*80}")
            print(f"🎯 Brand {i}/{len(brands)}: {brand_name}")
            print(f"{'='*80}")
            
            # Scrape this brand (don't save individually)
            brand_perfumes = self.scrape_by_brand(brand_name, limit_per_brand, save_to_file=False)
            all_perfumes.extend(brand_perfumes)
            
            print(f"\n📊 Progress: {len(all_perfumes)} total perfumes scraped so far")
        
        print(f"\n{'='*80}")
        print(f"✅ Completed scraping {len(all_perfumes)} perfumes from {len(brands)} brands")
        print(f"{'='*80}")
        
        # Save all results to file
        if save_to_file and all_perfumes:
            self.save_to_json(all_perfumes)
        
        return all_perfumes
    
    def scrape_perfume_by_url(self, perfume_url: str, save_to_file: bool = True) -> Optional[Dict[str, Any]]:
        """
        Scrape a specific perfume by its direct Fragrantica URL.
        
        Args:
            perfume_url: Direct URL to a Fragrantica perfume page
                        (e.g., "https://www.fragrantica.com/perfume/Xerjoff/White-On-White-Three-76333.html")
            save_to_file: Whether to save result to data.json
            
        Returns:
            Dictionary with perfume data or None if failed
        """
        # Validate URL
        if not perfume_url or 'fragrantica.com/perfume/' not in perfume_url:
            print(f"❌ Invalid Fragrantica perfume URL: {perfume_url}")
            return None
        
        print(f"🚀 Scraping perfume from URL: {perfume_url}")
        
        # Visit homepage first to establish session and get cookies
        print("🌐 Establishing session with Fragrantica...")
        try:
            # Rotate User-Agent for initial session
            self.session.headers['User-Agent'] = random.choice(self.user_agents)
            self.session.get(self.base_url, timeout=15)
            time.sleep(3)  # Brief pause after initial connection
        except Exception as e:
            print(f"⚠️  Warning: Could not establish initial session: {e}")
        
        # Extract perfume details
        perfume_data = self.extract_perfume_details(perfume_url)
        
        if not perfume_data or not perfume_data.get('name'):
            print(f"❌ Failed to extract perfume data from {perfume_url}")
            return None
        
        print(f"✅ Successfully scraped: {perfume_data.get('name', 'Unknown')} by {perfume_data.get('brand', 'Unknown')}")
        
        # Save to file
        if save_to_file and perfume_data:
            self.save_to_json([perfume_data])
        
        return perfume_data

    def _post_reviews_ajax(self, form: Dict[str, str], referer: str) -> Optional[Dict[str, Any]]:
        """POST reviews4perfume_v2 and decrypt the CryptoJS response with retry logic."""
        ts = int(time.time() * 1000)
        url = f"{self.base_url}/ajax.php?reviews4perfume_v2&{ts}"

        for attempt in range(1, self.max_retries + 1):
            try:
                self.session.headers["User-Agent"] = random.choice(self.user_agents)
                self.session.headers["Referer"] = referer
                self.session.headers["Origin"] = self.base_url
                self.session.headers["Accept"] = "application/json, text/plain, */*"
                self.session.headers["Content-Type"] = "application/x-www-form-urlencoded"
                self.session.headers["Sec-Fetch-Dest"] = "empty"
                self.session.headers["Sec-Fetch-Mode"] = "cors"
                self.session.headers["Sec-Fetch-Site"] = "same-origin"

                response = self.session.post(url, data=form, timeout=20)
                if response.status_code in (403, 429):
                    wait = min(45, 10 * attempt)
                    print(f"⚠️  Reviews AJAX returned status {response.status_code}. Resetting session & waiting {wait}s (attempt {attempt}/{self.max_retries})...")
                    time.sleep(wait)
                    self._reset_session()
                    continue

                response.raise_for_status()
                blob = response.json()
                if not isinstance(blob, dict) or "ct" not in blob:
                    raise ValueError("Unexpected reviews response (not a CryptoJS blob)")
                return decrypt_cryptojs_blob(blob)

            except Exception as e:
                wait = min(45, 10 * attempt)
                print(f"❌ Error in reviews AJAX: {e}. Resetting session & retrying in {wait}s (attempt {attempt}/{self.max_retries})...")
                time.sleep(wait)
                self._reset_session()

        print(f"❌ Max retries reached for reviews AJAX POST")
        return None

    def scrape_reviews(
        self,
        *,
        perfume_uuid: str,
        fragrantica_id: int,
        perfume_url: str,
        sentiment: str = "positive",
        pages: int = 5,
        page_delay: float = 2.5,
    ) -> Dict[str, Any]:
        """
        Scrape reviews for a perfume via reviews4perfume_v2 AJAX.

        Args:
            perfume_uuid: Our DB perfume UUID (stored on each review row)
            fragrantica_id: Fragrantica numeric perfume id
            perfume_url: Perfume page URL (used as referer / session warm-up)
            sentiment: 'positive' or 'negative'
            pages: Max pages to fetch (Fragrantica typically caps at 5)
            page_delay: Delay between paginated AJAX calls

        Returns:
            Dict with reviews list and scrape metadata
        """
        sentiment = (sentiment or "positive").strip().lower()
        if sentiment not in ("positive", "negative"):
            raise ValueError("sentiment must be 'positive' or 'negative'")

        pages = max(1, int(pages))
        print(
            f"📝 Scraping up to {pages} page(s) of {sentiment} reviews "
            f"for fragrantica_id={fragrantica_id}"
        )

        # Warm session / cookies by loading the perfume page first
        soup = self._get_page(perfume_url)
        if soup is None:
            raise RuntimeError(f"Failed to load perfume page: {perfume_url}")

        all_reviews: List[Dict[str, Any]] = []
        pages_fetched = 0
        has_more = False
        next_token: Optional[str] = None

        for page_idx in range(pages):
            if page_idx == 0:
                form = {
                    "action": "reviews4perfume_v2",
                    "perfume_id": str(fragrantica_id),
                    "sentiment": sentiment,
                }
            else:
                if not next_token:
                    break
                form = {
                    "action": "reviews4perfume_v2",
                    "token": next_token,
                }
                time.sleep(page_delay)

            print(f"📡 Reviews AJAX page {page_idx + 1}/{pages} ({sentiment})")
            payload = self._post_reviews_ajax(form, referer=perfume_url)
            if not payload:
                break

            batch = normalize_reviews_payload(
                payload,
                perfume_uuid=perfume_uuid,
                sentiment=sentiment,
            )
            all_reviews.extend(batch)
            pages_fetched += 1

            pagination = payload.get("pagination") or {}
            has_more = bool(pagination.get("has_more"))
            next_token = pagination.get("next_token")
            max_pages = pagination.get("max_pages")
            print(
                f"   → {len(batch)} reviews (total {len(all_reviews)}); "
                f"has_more={has_more} max_pages={max_pages}"
            )

            if not has_more or not next_token:
                break
            if max_pages is not None and pages_fetched >= int(max_pages):
                break

        # Deduplicate by fragrantica_review_id (keep first)
        seen = set()
        unique: List[Dict[str, Any]] = []
        for row in all_reviews:
            rid = row["fragrantica_review_id"]
            if rid in seen:
                continue
            seen.add(rid)
            unique.append(row)

        print(f"✅ Scraped {len(unique)} unique reviews across {pages_fetched} page(s)")
        return {
            "reviews": unique,
            "pages_fetched": pages_fetched,
            "has_more": has_more,
            "sentiment": sentiment,
            "fragrantica_id": fragrantica_id,
            "perfume_id": perfume_uuid,
        }

    def save_to_json(self, perfumes: List[Dict[str, Any]], filename: str = "data/data.json"):
        """
        Save perfume data to JSON file.
        
        Args:
            perfumes: List of perfume dictionaries
            filename: Output file path
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(perfumes, f, indent=2, ensure_ascii=False)
            
            print(f"💾 Saved {len(perfumes)} perfumes to {filename}")
        except Exception as e:
            print(f"❌ Error saving to file: {str(e)}")


def scrape_fragrantica(limit: int = 2) -> List[Dict[str, Any]]:
    """
    Convenience function to scrape Fragrantica perfumes.
    
    Args:
        limit: Number of perfumes to scrape
        
    Returns:
        List of perfume dictionaries
    """
    # Use longer delay for reliability (17 seconds base + randomization + progressive slowdown)
    scraper = FragranticaScraper(delay=17.0)
    return scraper.scrape_perfumes(limit=limit, save_to_file=True)


def scrape_fragrantica_by_brand(brand_name: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Convenience function to scrape perfumes from a specific brand.
    
    Args:
        brand_name: Name of the brand (e.g., "Jean Paul Gaultier")
        limit: Number of perfumes to scrape from this brand
        
    Returns:
        List of perfume dictionaries
    """
    scraper = FragranticaScraper(delay=17.0)
    return scraper.scrape_by_brand(brand_name, limit=limit, save_to_file=True)


def scrape_fragrantica_brands(brands: List[str], limit_per_brand: int = 100) -> List[Dict[str, Any]]:
    """
    Convenience function to scrape perfumes from multiple brands.
    
    Args:
        brands: List of brand names (e.g., ["Jean Paul Gaultier", "Xerjoff", "Creed"])
        limit_per_brand: Number of perfumes to scrape per brand
        
    Returns:
        Combined list of perfume dictionaries from all brands
    """
    scraper = FragranticaScraper(delay=17.0)
    return scraper.scrape_multiple_brands(brands, limit_per_brand=limit_per_brand, save_to_file=True)


def scrape_fragrantica_by_url(perfume_url: str) -> Optional[Dict[str, Any]]:
    """
    Convenience function to scrape a specific perfume by its URL.
    
    Args:
        perfume_url: Direct URL to a Fragrantica perfume page
                    (e.g., "https://www.fragrantica.com/perfume/Xerjoff/White-On-White-Three-76333.html")
        
    Returns:
        Dictionary with perfume data or None if failed
    """
    scraper = FragranticaScraper(delay=17.0)
    return scraper.scrape_perfume_by_url(perfume_url, save_to_file=True)


def scrape_fragrantica_reviews(
    *,
    perfume_uuid: str,
    fragrantica_id: int,
    perfume_url: str,
    sentiment: str = "positive",
    pages: int = 5,
) -> Dict[str, Any]:
    """
    Convenience function to scrape reviews for one perfume.

    Uses a shorter base delay than full perfume scrapes; pagination uses ~2.5s gaps.
    """
    scraper = FragranticaScraper(delay=5.0)
    return scraper.scrape_reviews(
        perfume_uuid=perfume_uuid,
        fragrantica_id=fragrantica_id,
        perfume_url=perfume_url,
        sentiment=sentiment,
        pages=pages,
    )


if __name__ == "__main__":
    # Example 1: Test the scraper with 2 popular perfumes
    print("🧪 Running scraper test...")
    print("\n" + "="*80)
    print("Example 1: Scraping popular perfumes")
    print("="*80)
    perfumes = scrape_fragrantica(limit=2)
    print(f"\n📊 Results: {len(perfumes)} perfumes scraped")
    
    if perfumes:
        print("\n📋 Sample perfume:")
        print(json.dumps(perfumes[0], indent=2))
    
    # Example 2: Scrape perfumes from a single brand
    # Uncomment to test:
    # print("\n" + "="*80)
    # print("Example 2: Scraping from a single brand (Jean Paul Gaultier)")
    # print("="*80)
    # brand_perfumes = scrape_fragrantica_by_brand("Jean Paul Gaultier", limit=5)
    # print(f"\n📊 Results: {len(brand_perfumes)} perfumes scraped")
    
    # Example 3: Scrape perfumes from multiple brands
    # Uncomment to test:
    # print("\n" + "="*80)
    # print("Example 3: Scraping from multiple brands")
    # print("="*80)
    # brands = ["Jean Paul Gaultier", "Xerjoff", "Creed"]
    # multi_brand_perfumes = scrape_fragrantica_brands(brands, limit_per_brand=5)
    # print(f"\n📊 Results: {len(multi_brand_perfumes)} perfumes scraped from {len(brands)} brands")

