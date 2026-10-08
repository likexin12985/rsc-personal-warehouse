"""Private candidate table handles; no Base registration or schema creation."""
from functools import lru_cache
from app.stock_scrap_persistence_schema import build_schema


@lru_cache(maxsize=1)
def tables():
    return build_schema()[0].tables
