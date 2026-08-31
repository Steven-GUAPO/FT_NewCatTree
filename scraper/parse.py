"""
parse.py  -  Product-detail HTML -> raw record.

FilmTools product pages are fully server-rendered, so a single requests GET plus
this parser yields everything the attribute stage needs. The output dict matches
the shape of data/sample_raw.json so `pipeline.process_records` treats live and
sample data identically.

Extraction sources, most reliable first:
  * <meta property="og:title"> / <meta name="meta-title">  -> title
  * <meta property="product:price:amount">                 -> price
  * the "More Information" specs table                      -> specs dict
  * "SKU" / "MPN" / "Brands" rows                           -> identifiers
  * the description / key-features block                    -> description text

Requires beautifulsoup4 (+ lxml recommended). Not used by the offline path.
"""

from __future__ import annotations

import re
from typing import Optional


def _soup(html: str):
    from bs4 import BeautifulSoup
    # lxml if available, else stdlib parser
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _meta(soup, *, prop=None, name=None) -> Optional[str]:
    if prop:
        el = soup.find("meta", attrs={"property": prop})
        if el and el.get("content"):
            return el["content"].strip()
    if name:
        el = soup.find("meta", attrs={"name": name})
        if el and el.get("content"):
            return el["content"].strip()
    return None


def _labelled_value(soup, label: str) -> Optional[str]:
    """
    Find a 'label: value' pair rendered either as adjacent <strong>label</strong>
    text or as a two-cell table row. FilmTools shows SKU/MPN both ways.
    """
    # table row form
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2 and cells[0].get_text(strip=True).lower() == label.lower():
            return cells[1].get_text(" ", strip=True)
    # strong-label form
    for strong in soup.find_all(["strong", "th", "span"]):
        if strong.get_text(strip=True).lower() == label.lower():
            sib = strong.find_next(string=True)
            nxt = strong.find_next(["td", "span", "div"])
            if nxt and nxt.get_text(strip=True):
                return nxt.get_text(" ", strip=True)
            if sib:
                return str(sib).strip()
    return None


def _parse_specs_table(soup) -> dict:
    """
    Turn the 'More Information' / 'Specifications' table into a flat dict.
    FilmTools nests a sub-table inside a 'Specifications' cell; we flatten both
    the outer rows (SKU, Brands, MPN, ...) and the inner spec rows.
    """
    specs: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        key = cells[0].get_text(" ", strip=True)
        val_cell = cells[1]
        # a nested spec table?
        inner = val_cell.find("table")
        if inner:
            for r in inner.find_all("tr"):
                cc = r.find_all(["th", "td"])
                if len(cc) >= 2:
                    k = cc[0].get_text(" ", strip=True)
                    v = cc[1].get_text(" ", strip=True)
                    if k and v:
                        specs[k] = v
            continue
        val = val_cell.get_text(" ", strip=True)
        if key and val and key.lower() not in ("specifications",):
            specs.setdefault(key, val)
    return specs


_PRICE_BODY_RX = re.compile(r"\$\s*([\d,]+\.\d{2})")


def _price(soup) -> Optional[float]:
    amt = _meta(soup, prop="product:price:amount")
    if amt:
        try:
            return float(amt.replace(",", ""))
        except ValueError:
            pass
    # visible price fallback (skip crossed-out "regular" prices when possible)
    el = soup.select_one("[data-price-type='finalPrice'] .price, .price-wrapper .price, .price")
    if el:
        m = _PRICE_BODY_RX.search(el.get_text())
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _description(soup) -> str:
    """Prefer the key-features / product description block; fall back to og:desc."""
    parts = []
    # bulleted key features are the densest source of specs
    desc_block = soup.select_one("#description, .product.attribute.description, .product-info-main .value")
    if desc_block:
        parts.append(desc_block.get_text(" ", strip=True))
    ogd = _meta(soup, prop="og:description") or _meta(soup, name="meta-description")
    if ogd:
        parts.append(ogd)
    text = " ".join(parts)
    return re.sub(r"\s+", " ", text).strip()[:2000]


def parse_product_html(html: str, url: str) -> dict:
    soup = _soup(html)

    title = (_meta(soup, prop="og:title")
             or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None))
    specs = _parse_specs_table(soup)

    brand = specs.pop("Brands", None) or specs.pop("Brand", None)
    mpn = specs.pop("MPN", None) or _labelled_value(soup, "MPN")
    sku = specs.pop("SKU", None) or _labelled_value(soup, "SKU")
    # drop noise rows we don't want in the specs blob
    for noise in ("Product Condition",):
        specs.pop(noise, None)

    return {
        "url": url,
        "title": title,
        "brand": brand,
        "mpn": mpn,
        "sku": sku,
        "price_usd": _price(soup),
        "source_category": None,   # PDPs don't carry the category path; left null
        "description": _description(soup),
        "specs": specs or None,
    }
