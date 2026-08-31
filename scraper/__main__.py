"""
CLI for the FilmTools storage pipeline.

    # process the bundled real sample (no network, stdlib only) -> output/
    python -m scraper --offline data/sample_raw.json --out output

    # live crawl of the whole Storage department (needs requirements.txt installed)
    python -m scraper --live --out output --max-products 500

    # live crawl of specific category paths only
    python -m scraper --live --category digital-storage/hard-drives-raid-storage/ssd-solid-state-drives.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import process_records, build_summary, write_outputs, run_offline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scraper", description="FilmTools storage catalog restructuring")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", metavar="RAW_JSON", default="data/sample_raw.json",
                      help="process an existing raw-records JSON file (default)")
    mode.add_argument("--live", action="store_true",
                      help="crawl filmtools.com live (requires requirements.txt)")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--category", action="append", default=None,
                    help="restrict live crawl to one or more category page paths (repeatable)")
    ap.add_argument("--max-products", type=int, default=None,
                    help="safety cap on how many products to fetch in live mode")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)

    if args.live:
        # Lazy import: only the live path needs the third-party stack.
        try:
            from . import fetch, parse
        except ImportError as e:  # pragma: no cover
            print(f"[!] live mode needs the packages in requirements.txt: {e}", file=sys.stderr)
            return 2
        crawler = fetch.FilmToolsCrawler(max_products=args.max_products)
        raw_records = crawler.crawl(categories=args.category, parse_fn=parse.parse_product_html)
        products = process_records(raw_records)
        summary = build_summary(products)
        written = write_outputs(products, summary, out_dir)
    else:
        summary = run_offline(Path(args.offline), out_dir)
        written = list(out_dir.glob("*"))

    print(json.dumps(summary, indent=2))
    print("\nWrote:")
    for w in sorted(set(written)):
        print(f"  {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
