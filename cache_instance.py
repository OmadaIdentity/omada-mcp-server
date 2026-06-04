"""
cache_instance.py — Shared OmadaCache singleton.

Import `cache` and `CACHE_ENABLED` from here instead of instantiating
OmadaCache directly in each module. This guarantees a single SQLite
connection pool across graphql.py, admin.py, etc.

Configuration (via .env):
    CACHE_ENABLED      true/false  (default: true)
    CACHE_TTL_SECONDS  integer     (default: 3600)
    CACHE_AUTO_CLEANUP true/false  (default: true)
"""

import logging
import os

logger = logging.getLogger(__name__)

CACHE_ENABLED: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
CACHE_AUTO_CLEANUP: bool = os.getenv("CACHE_AUTO_CLEANUP", "true").lower() == "true"

cache = None

if CACHE_ENABLED:
    try:
        from cache import OmadaCache
        cache = OmadaCache(default_ttl=CACHE_TTL_SECONDS, auto_cleanup=CACHE_AUTO_CLEANUP)
        logger.info(f"Cache enabled (TTL: {CACHE_TTL_SECONDS}s, auto-cleanup: {CACHE_AUTO_CLEANUP})")
    except Exception as exc:
        logger.warning(f"Cache initialisation failed — running without cache: {exc}")
        CACHE_ENABLED = False
        cache = None
else:
    logger.info("Cache disabled (CACHE_ENABLED=false)")
