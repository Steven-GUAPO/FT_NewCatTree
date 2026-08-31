# FilmTools Storage (scrape, structure, re-categorize)

Takes the FilmTools **Storage department**, pulls each product, extracts
structured attributes from unstructured titles/descriptions/spec-tables, and
files everything into a proposed **user-first taxonomy** (facets + workflow
landing pages). Ships with a runnable pipeline, a
real harvested sample, generated output, and a faceted browse demo.


## What's here

```
scraper/attributes.py   title/desc/specs -> normalized attributes (+provenance +confidence)
scraper/taxonomy.py     the proposed model + deterministic assignment
scraper/pipeline.py     orchestration + JSON/CSV/summary writers
scraper/fetch.py        polite live crawler (robots, cache, backoff, JS-aware enum)
scraper/parse.py        product-detail HTML -> raw record
output/demo_products.json   compact feed consumed by the HTML demo    
output/products.json    full structured output (per-product attributes +taxonomy)
output/products.csv     flat one-row-per-product view
output/summary.json     catalog counts, extraction coverage, review queue
demo/faceted_demo.html  self-contained faceted browser over the output
taxonomy.md             the taxonomy spec + argument for it
```

## What I'd change with more time

- **LLM fallback.** Deterministic rules cover the big brands;
  a constrained JSON-schema output would handle odd phrasings and
  off-brand SKUs, gated by a confidence threshold and cross-checked against the
  spec table.
- **Learn workflow rules from behavior.** Seed workflows with rules, then tune
  them against real add-to-cart / search data. Can be done with machine learning algorithms and predictive models. 
- **Unit tests + schema validation** on every output record, and a diffing step
  so a site redesign that breaks a selector fails loudly.


## Where it breaks at 10,000 products

- **Runtime speed** At 1–3 s/request, ~1,000 products (politeness delays + Playwright category rendering) ran on the order of an hour-plus. Scaling at 10,000 would land around 10-12 hours single-threaded.
- **Extraction accuracy decays on unseen brands.** Regexes tuned on OWC/LaCie/
SanDisk will miss quirks from the 25+ other brands. This is where a labeled
eval set and a human review queue (thresholded on confidence) matter most. Couple hundred products to need review.
- **Memory & storage.** products.json came out to 3.4MB for 1,013 products. Extrapolated, 10,000 products is roughly 34MB of JSON. The in-memory Python object overhead during processing is larger than the serialized size, but even generously (5-10x) that's a few hundred MB, not a real problem at 10k. However, .cache/ came out to 450MB across ~1,000 files for the 1,013-product crawl. Scaled to 10,000 products, that's roughly 4-4.5GB of raw cached HTML showing that at that scale you'd want the cache pruned or moved off local disk rather than left to grow unbounded run over run.
