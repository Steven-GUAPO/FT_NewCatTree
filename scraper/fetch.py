"""
fetch.py  -  Polite live crawler for the FilmTools Storage department.

This is the only stage that talks to the network. It is intentionally
conservative: the goal of the exercise is a clean dataset, not speed, and it is
someone else's storefront.

Politeness / good-citizen behaviour:
  * obeys robots.txt (urllib.robotparser)
  * one request at a time (no concurrency), randomized 1-3 s delay between hits
  * a descriptive User-Agent with contact info, so their ops team can reach out
  * exponential backoff on 429/5xx, and it honours Retry-After
  * an on-disk response cache so re-runs never re-hit a page already seen

Enumeration reality (discovered during recon):
  * FilmTools category/listing pages render their product grid in JavaScript -
    the server HTML contains only nav/breadcrumb/footer. So a plain requests GET
    of a category page yields NO products. Two enumeration paths are provided:
        1. render_category()  - headless Chromium via Playwright, the robust path
        2. sitemap_product_urls() - parse /media/sitemap.xml, filter storage-ish
           slugs. Lighter, but slugs are flat (no category), so it is approximate.
  * Product DETAIL pages ARE fully server-rendered (meta tags + a specs table),
    so once a product URL is known, a simple requests GET + parse.py is enough.

Requires: requests, beautifulsoup4, lxml, and (for path 1) playwright. See
requirements.txt. Nothing here runs in the offline sample path.
"""

from __future__ import annotations

import hashlib
import random
import time
import urllib.robotparser
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin, urlparse

BASE = "https://www.filmtools.com"
SITEMAP = f"{BASE}/media/sitemap.xml"
MAX_CATEGORY_PAGES = 50  # safety cap on paginated clicks per category

# The Storage department leaf categories (from the live nav during recon).
STORAGE_CATEGORY_PATHS = [
    "digital-storage/hard-drives-raid-storage/portable-drives.html",
    "digital-storage/hard-drives-raid-storage/desktop-drives.html",
    "digital-storage/hard-drives-raid-storage/raid-drives.html",
    "digital-storage/hard-drives-raid-storage/ssd-solid-state-drives.html",
    "digital-storage/hard-drives-raid-storage/internal-drives.html",
    "digital-storage/hard-drives-raid-storage/network-storage.html",
    "digital-storage/memory-cards/compact-flash-memory-cards.html",
    "digital-storage/memory-cards/sd-microsd-memory-cards.html",
    "digital-storage/memory-cards/memory-card-readers.html",
    "digital-storage/flash-drives/usb-2-0-flash-drives.html",
    "digital-storage/flash-drives/usb-3-0-flash-drives.html",
    "digital-storage/data-archiving/lto.html",
    "digital-storage/data-archiving/blu-ray.html",
    "digital-storage/data-archiving/dvd.html",
    "digital-storage/data-archiving/cd.html",
    "digital-storage/digital-recorders.html",
    "digital-storage/rugged-drives.html",
    "digital-storage/thunderbolt-drives.html",
    "digital-storage/solid-state-drives.html",
    "digital-storage/docks.html",
    "digital-storage/cables.html",
]

# Slug keywords used only by the sitemap fallback to guess storage products.
_STORAGE_SLUG_HINTS = (
    "ssd", "nvme", "hard-drive", "hard-drives", "raid", "thunderbolt", "usb4",
    "cfexpress", "cfast", "sdxc", "sd-card", "microsd", "memory-card", "card-reader",
    "flash-drive", "lto", "tape", "dock", "envoy", "express-1m2", "rugged",
    "2big", "6big", "d2-", "mercury-", "extreme-portable", "portable-ssd",
)

USER_AGENT = (
    "FilmToolsCatalogBot/0.1 (+catalog-restructure exercise; contact: you@example.com) "
    "python-requests"
)


class FilmToolsCrawler:
    def __init__(self, cache_dir: str = ".cache", min_delay: float = 1.0,
                 max_delay: float = 3.0, max_products: Optional[int] = None,
                 use_playwright: bool = True):
        import requests  # local import so offline path never needs it
        self.requests = requests
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache = Path(cache_dir)
        self.cache.mkdir(exist_ok=True)
        self.min_delay, self.max_delay = min_delay, max_delay
        self.max_products = max_products
        self.use_playwright = use_playwright
        self._rp = self._load_robots()

    # -- robots -------------------------------------------------------------- #
    def _load_robots(self):
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{BASE}/robots.txt")
        try:
            rp.read()
        except Exception:
            pass  # if robots is unreachable, stay conservative and continue slowly
        return rp

    def _allowed(self, url: str) -> bool:
        try:
            return self._rp.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    # -- low level GET with cache + backoff ---------------------------------- #
    def _cache_path(self, url: str) -> Path:
        return self.cache / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")

    def get(self, url: str, max_retries: int = 4) -> Optional[str]:
        cp = self._cache_path(url)
        if cp.exists():
            return cp.read_text(errors="ignore")
        if not self._allowed(url):
            print(f"[robots] disallowed, skipping: {url}")
            return None

        delay = 2.0
        for attempt in range(max_retries):
            time.sleep(random.uniform(self.min_delay, self.max_delay))
            try:
                r = self.session.get(url, timeout=30)
            except self.requests.RequestException as e:
                print(f"[warn] {e} (attempt {attempt+1})")
                time.sleep(delay); delay *= 2; continue
            if r.status_code == 200:
                cp.write_text(r.text, errors="ignore")
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                wait = float(r.headers.get("Retry-After", delay))
                print(f"[backoff] {r.status_code} on {url}; waiting {wait:.0f}s")
                time.sleep(wait); delay *= 2; continue
            print(f"[warn] HTTP {r.status_code} on {url}")
            return None
        return None

    # -- enumeration path 1: headless render --------------------------------- #
    def render_category(self, category_url: str) -> list[str]:
        """
        Render a JS category page with Playwright and collect product-detail URLs
        across all paginated pages. Returns [] if Playwright isn't installed.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[info] Playwright not installed; skipping headless enumeration.")
            return []

        urls: list[str] = []
        seen_hrefs: set[str] = set()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(category_url, wait_until="networkidle", timeout=45000)

            for page_num in range(1, MAX_CATEGORY_PAGES + 1):
                # product tiles link to PDPs. Legacy Magento Luma uses .product-item-link;
                # the current storefront (a client-rendered "ds-sdk" theme) wraps each tile
                # in <div class="ds-sdk-product-item"><a href="...slug.html">...</a></div>
                # with no semantic link class, so match on that container instead.
                tiles = page.query_selector_all(
                    "a.product-item-link, li.product-item a[href$='.html'], "
                    ".ds-sdk-product-item a[href$='.html']"
                )
                page_hrefs = [h for h in (a.get_attribute("href") for a in tiles) if h]
                new_hrefs = [h for h in page_hrefs if h not in seen_hrefs]
                seen_hrefs.update(new_hrefs)
                urls.extend(urljoin(BASE, h) for h in new_hrefs)

                # legacy Luma pager: a real link, so a plain goto advances it.
                legacy_next = page.query_selector("a.action.next, li.pages-item-next a")
                if legacy_next:
                    href = legacy_next.get_attribute("href")
                    if not href:
                        break
                    page.goto(urljoin(BASE, href), wait_until="networkidle", timeout=45000)
                    time.sleep(random.uniform(self.min_delay, self.max_delay))
                    continue

                # ds-sdk pager: numbered <li> items with no href, advanced by
                # clicking the "next" chevron (the arrow rotated the opposite way
                # from the "prev" one, which carries a plain "rotate-90" class).
                if not new_hrefs:
                    break
                next_arrow = page.query_selector(".ds-plp-pagination [class*='-rotate-90']")
                if not next_arrow or "cursor-not-allowed" in (next_arrow.get_attribute("class") or ""):
                    break
                next_arrow.click()
                try:
                    page.wait_for_function(
                        "n => document.querySelector('.ds-plp-pagination__item--current')"
                        "?.textContent.trim() === String(n)",
                        arg=page_num + 1,
                        timeout=15000,
                    )
                except Exception:
                    pass  # fall back to the "no new hrefs" check on the next loop iteration
                page.wait_for_load_state("networkidle", timeout=45000)
                time.sleep(random.uniform(self.min_delay, self.max_delay))
            browser.close()
        # dedupe, keep order
        return list(dict.fromkeys(urls))

    # -- enumeration path 2: sitemap fallback -------------------------------- #
    def sitemap_product_urls(self) -> list[str]:
        """
        Parse the XML sitemap(s) and keep URLs whose slug looks like storage.
        Approximate (slugs carry no category), but needs no browser and is a good
        completeness cross-check against the rendered category pages.
        """
        import re
        from xml.etree import ElementTree as ET

        def _locs(xml_text):
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                return []
            return [el.text for el in root.iter() if el.tag.endswith("loc") and el.text]

        out: list[str] = []
        index_xml = self.get(SITEMAP)
        if not index_xml:
            return out
        locs = _locs(index_xml)
        sub_sitemaps = [u for u in locs if u.endswith(".xml")]
        targets = sub_sitemaps or [SITEMAP]
        for sm in targets:
            xml = self.get(sm) if sm != SITEMAP else index_xml
            for u in _locs(xml or ""):
                if u.endswith(".xml"):
                    continue
                slug = urlparse(u).path.lower()
                if any(h in slug for h in _STORAGE_SLUG_HINTS):
                    out.append(u)
        return list(dict.fromkeys(out))

    # -- top level ----------------------------------------------------------- #
    def enumerate_products(self, categories: Optional[Iterable[str]] = None) -> list[str]:
        paths = list(categories) if categories else STORAGE_CATEGORY_PATHS
        urls: list[str] = []
        if self.use_playwright:
            for path in paths:
                cat_url = path if path.startswith("http") else f"{BASE}/{path}"
                found = self.render_category(cat_url)
                print(f"[enum] {len(found):4d} products <- {cat_url}")
                urls.extend(found)
        if not urls:
            print("[enum] falling back to sitemap enumeration")
            urls = self.sitemap_product_urls()
        return list(dict.fromkeys(urls))

    def crawl(self, categories=None, parse_fn: Callable[[str, str], dict] = None) -> list[dict]:
        product_urls = self.enumerate_products(categories)
        if self.max_products:
            product_urls = product_urls[: self.max_products]
        print(f"[crawl] fetching {len(product_urls)} product pages")
        records = []
        for i, url in enumerate(product_urls, 1):
            html = self.get(url)
            if not html:
                continue
            try:
                rec = parse_fn(html, url) if parse_fn else {"url": url, "title": None}
                if rec and rec.get("title"):
                    records.append(rec)
            except Exception as e:  # never let one bad page kill the run
                print(f"[parse-error] {url}: {e}")
            if i % 25 == 0:
                print(f"  ...{i}/{len(product_urls)}")
        return records
