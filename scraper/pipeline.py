"""
pipeline.py  -  End-to-end orchestration.

Offline mode (default, no network, stdlib only):
    read data/sample_raw.json  ->  extract attributes  ->  assign taxonomy
    ->  write output/products.json, output/products.csv, output/summary.json

Live mode is wired through scraper.__main__ and calls scraper.fetch +
scraper.parse to produce the same raw-record shape this file consumes, so the
attribute/taxonomy/output stages are identical for sample and full crawl.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import attributes, taxonomy


def process_records(records: list[dict]) -> list[dict]:
    """raw record -> enriched product (identity + attributes + taxonomy)."""
    out = []
    for rec in records:
        attrs = attributes.extract_all(rec)
        cats = taxonomy.assign(attrs)
        out.append({
            "identity": {
                "title": rec.get("title"),
                "url": rec.get("url"),
                "brand": rec.get("brand") or attrs["brand"]["value"],
                "mpn": rec.get("mpn"),
                "sku": rec.get("sku"),
                "price_usd": rec.get("price_usd"),
                "source_category_live": rec.get("source_category"),  # FilmTools' current tree, for comparison
            },
            "attributes": attrs,
            "taxonomy": cats,
        })
    return out


def build_summary(products: list[dict]) -> dict:
    """Aggregate view: catalog counts, facet coverage, extraction quality, review queue."""
    primary = Counter(p["taxonomy"]["primary_path"] for p in products)
    workflows = Counter(w for p in products for w in p["taxonomy"]["workflows"])
    media = Counter(p["attributes"]["media_type"]["value"] for p in products)
    interfaces = Counter(
        i["family"]
        for p in products
        for i in (p["attributes"]["interfaces"]["value"] or {}).get("interfaces", [])
    )
    capacity_buckets = Counter(
        p["taxonomy"]["facets"]["capacity_bucket"] for p in products
        if p["taxonomy"]["facets"]["capacity_bucket"]
    )

    # extraction quality: how often each core field was populated, and mean confidence
    core_fields = ["media_type", "form_factor", "capacity", "interfaces", "speed"]
    coverage = {}
    for f in core_fields:
        filled = sum(1 for p in products if p["attributes"][f]["value"] not in (None, {}, []))
        coverage[f] = {
            "filled_pct": round(100 * filled / len(products), 1),
            "mean_confidence": round(
                sum(p["attributes"][f]["confidence"] for p in products) / len(products), 2
            ),
        }

    review_queue = [
        {"title": p["identity"]["title"], "reason": p["attributes"]["_qa_notes"] or "low completeness"}
        for p in products if p["taxonomy"]["needs_review"] or p["attributes"]["_qa_notes"]
    ]

    return {
        "product_count": len(products),
        "primary_category_counts": dict(primary),
        "workflow_counts": dict(workflows),
        "media_type_counts": dict(media),
        "interface_counts": dict(interfaces),
        "capacity_bucket_counts": dict(capacity_buckets),
        "extraction_coverage": coverage,
        "review_queue_size": len(review_queue),
        "review_queue": review_queue,
    }


# --------------------------------------------------------------------------- #
#  Output writers
# --------------------------------------------------------------------------- #

_CSV_COLUMNS = [
    "title", "brand", "primary_category", "primary_subcategory",
    "media_type", "form_factor", "capacity_display", "capacity_gb",
    "interfaces", "connectors", "read_mbps", "write_mbps", "rpm",
    "bays", "raid_levels", "rugged", "ip_rating", "drop_m",
    "encryption", "bus_powered", "warranty_years", "workflows",
    "price_usd", "completeness", "needs_review", "url",
]


def _flatten(p: dict) -> dict:
    a, t = p["attributes"], p["taxonomy"]
    cap = a["capacity"]["value"] or {}
    ifaces = (a["interfaces"]["value"] or {})
    speed = a["speed"]["value"] or {}
    bays = a["bays_raid"]["value"] or {}
    rug = a["rugged"]["value"] or {}
    return {
        "title": p["identity"]["title"],
        "brand": p["identity"]["brand"],
        "primary_category": t["primary"][0],
        "primary_subcategory": t["primary"][1],
        "media_type": a["media_type"]["value"],
        "form_factor": a["form_factor"]["value"],
        "capacity_display": cap.get("display"),
        "capacity_gb": cap.get("value_gb"),
        "interfaces": "; ".join(i["family"] for i in ifaces.get("interfaces", [])) or None,
        "connectors": "; ".join(ifaces.get("connectors") or []) or None,
        "read_mbps": speed.get("read_mbps"),
        "write_mbps": speed.get("write_mbps"),
        "rpm": speed.get("rpm"),
        "bays": bays.get("bays"),
        "raid_levels": "; ".join(map(str, bays.get("raid_levels") or [])) or None,
        "rugged": rug.get("rugged", False),
        "ip_rating": rug.get("ip_rating"),
        "drop_m": rug.get("drop_m"),
        "encryption": a["encryption"]["value"],
        "bus_powered": a["bus_powered"]["value"],
        "warranty_years": a["warranty_years"]["value"],
        "workflows": "; ".join(t["workflows"]) or None,
        "price_usd": p["identity"]["price_usd"],
        "completeness": a["_completeness"],
        "needs_review": t["needs_review"],
        "url": p["identity"]["url"],
    }


def write_outputs(products: list[dict], summary: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    pj = out_dir / "products.json"
    pj.write_text(json.dumps({"summary": summary, "products": products}, indent=2), encoding="utf-8")
    written.append(pj)

    pc = out_dir / "products.csv"
    with pc.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        w.writeheader()
        for p in products:
            w.writerow(_flatten(p))
    written.append(pc)

    sj = out_dir / "summary.json"
    sj.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    written.append(sj)

    # a compact feed the HTML demo can load without a build step
    demo_feed = out_dir / "demo_products.json"
    demo_feed.write_text(json.dumps([_flatten(p) | {
        "capacity_bucket": p["taxonomy"]["facets"]["capacity_bucket"],
        "speed_tier": p["taxonomy"]["facets"]["speed_tier"],
    } for p in products], indent=2), encoding="utf-8")
    written.append(demo_feed)

    return written


def run_offline(raw_path: Path, out_dir: Path) -> dict:
    raw = json.loads(Path(raw_path).read_text())
    records = raw["products"] if isinstance(raw, dict) else raw
    products = process_records(records)
    summary = build_summary(products)
    write_outputs(products, summary, out_dir)
    return summary
