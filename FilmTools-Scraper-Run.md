# FilmTools Scraper — Run Guide

This document covers everything needed to set up the environment and
run the scraper end-to-end.

## Prerequisites

- **Python 3.13** (developed and tested against 3.13.2). No other language
  runtime is required.
- **pip** for the live-crawl dependencies.

## Project layout

All commands below assume your working directory is the folder that directly
contains `scraper/` and `data/` i.e. `filmtools-storage/`.
If you're navigating from a parent folder, `cd` into it first:

```bash
cd filmtools-storage
ls    # should show: scraper/  data/  demo/  output/  README.md  requirements.txt  taxonomy.md
```

## Dependencies

| Live crawl | `requirements.txt`: `requests`, `beautifulsoup4`, `lxml`, `playwright`, plus the Playwright Chromium browser binary |

`requirements.txt` in full:

```
requests>=2.31          # HTTP for product-detail pages
beautifulsoup4>=4.12    # HTML parsing (parse.py)
lxml>=5.0               # faster/robuster BeautifulSoup backend
playwright>=1.44        # headless enumeration of JS-rendered category pages
```

## Setup

```bash
# 1. (optional) create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate    # macOS/Linux

# 2. Install live-crawl dependencies
python -m pip install -r requirements.txt

# 3. Download the Playwright Chromium browser binary (first time)
python -m playwright install chromium
```

## Running

### Live crawl

Crawls the real Storage department on filmtools.com. The crawler is
deliberately polite (see below), so treat runtime accordingly — expect an 
hour or more for a full storage department crawl of ~1,000 products. 

```bash
# Single category — fast, good for a first validation run
python -m scraper --live \
  --category digital-storage/hard-drives-raid-storage/ssd-solid-state-drives.html \
  --out output

# Full department
python -m scraper --live --out output

# Safety cap on total products fetched, useful for a bounded test run
python -m scraper --live --out output --max-products 50
```



## Output

Live crawl writes four files to the output directory:

| File | Contents |
|---|---|
| `products.json` | Full structured output: per-product attributes (with provenance + confidence values) and taxonomy assignment |
| `products.csv` | Flat, one-row-per-product view of the same data |
| `summary.json` | Catalog counts, extraction-quality coverage, and the review queue |
| `demo_products.json` | Compact feed consumed by the HTML demo |

To browse the result, open `demo/faceted_demo.html` directly in any browser.
The product data is embedded in the file so no server or build step is
needed.



## Troubleshooting

**Live crawl enumerates zero (or very few) products.** The category-page
enumeration in `render_category()` (`scraper/fetch.py`) depends on the
current CSS structure of filmtools.com's storefront theme. If the site's
frontend changes, the tile selector can stop matching. Check the selector
against the live page's rendered DOM (e.g. via browser devtools or a quick
Playwright script) and update it in `render_category()` accordingly. This is
a real maintenance point and the selector needed exactly
this kind of update during this project's development.

**`ModuleNotFoundError` for `playwright`, `bs4`, or `lxml` in live mode.**
Run `python -m pip install -r requirements.txt` from the project root.

**Playwright errors about a missing browser executable.** Run
`python -m playwright install chromium`.

## Pre-upload checklist

- [ ] `.gitignore` is present and excludes `.cache/` and `__pycache__/`.
- [ ] No scratch/verification output directories are present beyond `output/`.
- [ ] `output/` contains the intended result (check `output/summary.json` for
      the expected product count).
