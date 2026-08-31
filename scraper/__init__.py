"""
FilmTools storage catalog restructuring pipeline.

Stages:
    fetch      polite crawl / enumeration of the FilmTools Storage department (live)
    parse      product-detail HTML -> raw record (live)
    attributes raw record -> normalized attributes with provenance + confidence
    taxonomy   attributes -> proposed primary category + facets + workflows
    pipeline   orchestration + JSON/CSV/summary output

`attributes`, `taxonomy` and the offline path in `pipeline` use only the Python
standard library so they run anywhere. `fetch`/`parse` need the third-party
packages in requirements.txt and are only used for the live crawl.
"""

__all__ = ["attributes", "taxonomy", "pipeline"]
__version__ = "0.1.0"
