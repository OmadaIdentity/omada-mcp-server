# cache.py
"""
SQLite-based caching system for Omada MCP Server API responses.

Features:
- TTL-based expiration (default: 1 hour)
- Clear visibility logging for cache hits/misses
- Optimized identity lookups by email/UId
- Cache statistics and management
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OmadaCache:
    """SQLite-based cache for Omada API responses with TTL support."""

    def __init__(
        self, db_path: str = None, default_ttl: int = 3600, auto_cleanup: bool = True
    ):
        """
        Initialize the cache.

        Args:
            db_path: Path to SQLite database file (default: omada_cache.db in script directory)
            default_ttl: Default time-to-live in seconds (default: 3600 = 1 hour)
            auto_cleanup: Enable automatic cleanup of expired entries (default: True)
        """
        if not db_path:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(script_dir, "omada_cache.db")

        self.db_path = db_path
        self.default_ttl = default_ttl
        self.auto_cleanup = auto_cleanup
        self._cleanup_task = None
        self._cleanup_running = False
        self._init_db()

        # Start automatic cleanup if enabled
        if self.auto_cleanup:
            self.start_auto_cleanup()
            logger.info(
                f"Cache initialized at: {self.db_path} (default TTL: {default_ttl}s, auto-cleanup: ENABLED)"
            )
        else:
            logger.info(
                f"Cache initialized at: {self.db_path} (default TTL: {default_ttl}s, auto-cleanup: DISABLED)"
            )

    def _init_db(self):
        """Initialize cache database with tables."""
        conn = sqlite3.connect(self.db_path)

        # Enable WAL mode for better concurrent read performance and crash safety.
        # WAL allows readers and writers to coexist without blocking each other.
        conn.execute("PRAGMA journal_mode=WAL")

        cursor = conn.cursor()

        # Main cache table with TTL
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                query_params TEXT,
                response_data TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                hit_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP
            )
        """
        )

        # Identity lookup table (optimized for email/UId lookups)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_cache (
                uid TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                identity_id TEXT,
                display_name TEXT,
                first_name TEXT,
                last_name TEXT,
                full_data TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                hit_count INTEGER DEFAULT 0
            )
        """
        )

        # Resource type cache (very static)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_type_cache (
                resource_type_id INTEGER PRIMARY KEY,
                resource_type_name TEXT,
                system_id INTEGER,
                full_data TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                hit_count INTEGER DEFAULT 0
            )
        """
        )

        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email ON identity_cache(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_endpoint ON api_cache(endpoint)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_expires ON api_cache(expires_at)"
        )

        conn.commit()
        conn.close()
        logger.debug("Cache database tables initialized")

    def _generate_cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Generate deterministic cache key from endpoint and parameters."""
        # Sort params to ensure consistent key generation
        param_str = json.dumps(params, sort_keys=True)
        key_input = f"{endpoint}:{param_str}"
        return hashlib.sha256(key_input.encode()).hexdigest()

    def get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Get cached response if exists and not expired.

        Returns:
            Cached response dict or None if not found/expired
        """
        cache_key = self._generate_cache_key(endpoint, params)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT response_data, expires_at, created_at
            FROM api_cache
            WHERE cache_key = ? AND expires_at > ?
        """,
            (cache_key, datetime.now()),
        )

        row = cursor.fetchone()

        if row:
            # Update hit count and last accessed
            cursor.execute(
                """
                UPDATE api_cache
                SET hit_count = hit_count + 1, last_accessed = ?
                WHERE cache_key = ?
            """,
                (datetime.now(), cache_key),
            )
            conn.commit()

            response_data = json.loads(row[0])
            created_at = datetime.fromisoformat(row[2])
            age_seconds = (datetime.now() - created_at).total_seconds()

            logger.info(f"🎯 CACHE HIT for {endpoint} (age: {age_seconds:.1f}s)")
            logger.debug(
                f"DEBUG: Cache HIT - endpoint={endpoint}, cache_key={cache_key[:16]}..., age={age_seconds:.1f}s, created={created_at.isoformat()}"
            )
            conn.close()

            # Add cache metadata to response
            response_data["_cache_metadata"] = {
                "cached": True,
                "cache_hit": True,
                "created_at": created_at.isoformat(),
                "age_seconds": age_seconds,
            }

            return response_data

        logger.info(f"❌ CACHE MISS for {endpoint} - fetching from API")
        logger.debug(
            f"DEBUG: Cache MISS - endpoint={endpoint}, cache_key={cache_key[:16]}..., reason=not_found_or_expired"
        )
        conn.close()
        return None

    def set(
        self,
        endpoint: str,
        params: Dict[str, Any],
        response: Dict[str, Any],
        ttl_seconds: int = None,
    ):
        """
        Store response in cache with TTL.

        Args:
            endpoint: API endpoint name
            params: Query parameters dict
            response: Response data to cache
            ttl_seconds: Time-to-live in seconds (uses default_ttl if not specified)
        """
        if ttl_seconds is None:
            ttl_seconds = self.default_ttl

        cache_key = self._generate_cache_key(endpoint, params)
        now = datetime.now()
        expires_at = now + timedelta(seconds=ttl_seconds)

        # Remove cache metadata before storing (avoid nested metadata)
        response_copy = response.copy()
        response_copy.pop("_cache_metadata", None)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO api_cache
            (cache_key, endpoint, query_params, response_data, created_at, expires_at, hit_count, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
            (
                cache_key,
                endpoint,
                json.dumps(params, sort_keys=True),
                json.dumps(response_copy),
                now,
                expires_at,
                now,
            ),
        )

        conn.commit()
        conn.close()

        logger.info(
            f"💾 CACHE STORED for {endpoint} (TTL: {ttl_seconds}s, expires: {expires_at.strftime('%H:%M:%S')})"
        )
        logger.debug(
            f"DEBUG: Cache STORED - endpoint={endpoint}, cache_key={cache_key[:16]}..., ttl={ttl_seconds}s, expires={expires_at.isoformat()}"
        )

    def cache_identity(self, identity_data: Dict[str, Any], ttl_seconds: int = None):
        """
        Cache identity data with optimized lookup fields.

        Args:
            identity_data: Identity dict with UId, EMAIL, etc.
            ttl_seconds: Time-to-live in seconds (uses default_ttl if not specified)
        """
        if ttl_seconds is None:
            ttl_seconds = self.default_ttl

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now()
        expires_at = now + timedelta(seconds=ttl_seconds)

        uid = identity_data.get("UId")
        email = identity_data.get("EMAIL")

        cursor.execute(
            """
            INSERT OR REPLACE INTO identity_cache
            (uid, email, identity_id, display_name, first_name, last_name, full_data, created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
            (
                uid,
                email,
                identity_data.get("IDENTITYID"),
                identity_data.get("DISPLAYNAME"),
                identity_data.get("FIRSTNAME"),
                identity_data.get("LASTNAME"),
                json.dumps(identity_data),
                now,
                expires_at,
            ),
        )

        conn.commit()
        conn.close()

        logger.info(
            f"💾 IDENTITY CACHED: {email} (UId: {uid[:8]}..., TTL: {ttl_seconds}s)"
        )

    def get_identity_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Fast lookup of identity by email.

        Args:
            email: Email address to lookup

        Returns:
            Identity dict or None if not found/expired
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT full_data, created_at
            FROM identity_cache
            WHERE email = ? AND expires_at > ?
        """,
            (email, datetime.now()),
        )

        row = cursor.fetchone()

        if row:
            # Update hit count
            cursor.execute(
                """
                UPDATE identity_cache
                SET hit_count = hit_count + 1
                WHERE email = ?
            """,
                (email,),
            )
            conn.commit()

            identity_data = json.loads(row[0])
            created_at = datetime.fromisoformat(row[1])
            age_seconds = (datetime.now() - created_at).total_seconds()

            logger.info(
                f"🎯 IDENTITY CACHE HIT for email: {email} (age: {age_seconds:.1f}s)"
            )
            conn.close()

            # Add cache metadata
            identity_data["_cache_metadata"] = {
                "cached": True,
                "cache_hit": True,
                "created_at": created_at.isoformat(),
                "age_seconds": age_seconds,
            }

            return identity_data

        logger.info(f"❌ IDENTITY CACHE MISS for email: {email}")
        conn.close()
        return None

    def get_identity_by_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """
        Fast lookup of identity by UId.

        Args:
            uid: UId (GUID) to lookup

        Returns:
            Identity dict or None if not found/expired
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT full_data, created_at
            FROM identity_cache
            WHERE uid = ? AND expires_at > ?
        """,
            (uid, datetime.now()),
        )

        row = cursor.fetchone()

        if row:
            # Update hit count
            cursor.execute(
                """
                UPDATE identity_cache
                SET hit_count = hit_count + 1
                WHERE uid = ?
            """,
                (uid,),
            )
            conn.commit()

            identity_data = json.loads(row[0])
            created_at = datetime.fromisoformat(row[1])
            age_seconds = (datetime.now() - created_at).total_seconds()

            logger.info(
                f"🎯 IDENTITY CACHE HIT for UId: {uid[:8]}... (age: {age_seconds:.1f}s)"
            )
            conn.close()

            # Add cache metadata
            identity_data["_cache_metadata"] = {
                "cached": True,
                "cache_hit": True,
                "created_at": created_at.isoformat(),
                "age_seconds": age_seconds,
            }

            return identity_data

        logger.info(f"❌ IDENTITY CACHE MISS for UId: {uid[:8]}...")
        conn.close()
        return None

    def invalidate(self, endpoint: str = None, params: Dict[str, Any] = None):
        """
        Invalidate specific cache entry or all entries for an endpoint.

        Args:
            endpoint: Endpoint to clear (optional)
            params: Specific parameters to clear (optional, requires endpoint)

        Returns:
            Number of entries deleted
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if params and endpoint:
            cache_key = self._generate_cache_key(endpoint, params)
            cursor.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
            deleted = cursor.rowcount
            logger.info(
                f"🗑️ CACHE INVALIDATED: {endpoint} (specific params) - {deleted} entries deleted"
            )
        elif endpoint:
            cursor.execute("DELETE FROM api_cache WHERE endpoint = ?", (endpoint,))
            deleted = cursor.rowcount
            logger.info(f"🗑️ CACHE INVALIDATED: {endpoint} - {deleted} entries deleted")
        else:
            cursor.execute("DELETE FROM api_cache")
            api_deleted = cursor.rowcount
            cursor.execute("DELETE FROM identity_cache")
            identity_deleted = cursor.rowcount
            cursor.execute("DELETE FROM resource_type_cache")
            resource_deleted = cursor.rowcount
            deleted = api_deleted + identity_deleted + resource_deleted
            logger.info(f"🗑️ ENTIRE CACHE CLEARED - {deleted} total entries deleted")

        conn.commit()
        conn.close()

        return deleted

    def cleanup_expired(self):
        """
        Remove expired cache entries.

        Returns:
            Number of expired entries removed
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        now = datetime.now()
        cursor.execute("DELETE FROM api_cache WHERE expires_at < ?", (now,))
        api_expired = cursor.rowcount

        cursor.execute("DELETE FROM identity_cache WHERE expires_at < ?", (now,))
        identity_expired = cursor.rowcount

        cursor.execute("DELETE FROM resource_type_cache WHERE expires_at < ?", (now,))
        resource_expired = cursor.rowcount

        total_deleted = api_expired + identity_expired + resource_expired

        conn.commit()
        conn.close()

        if total_deleted > 0:
            logger.info(f"🧹 CLEANUP: Removed {total_deleted} expired cache entries")

        return total_deleted

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache statistics and performance metrics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # API cache stats
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_entries,
                COUNT(CASE WHEN expires_at > ? THEN 1 END) as valid_entries,
                COUNT(CASE WHEN expires_at <= ? THEN 1 END) as expired_entries,
                SUM(hit_count) as total_hits,
                AVG(hit_count) as avg_hits_per_entry
            FROM api_cache
        """,
            (datetime.now(), datetime.now()),
        )

        api_stats = cursor.fetchone()

        # Identity cache stats
        cursor.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN expires_at > ? THEN 1 END) as valid,
                SUM(hit_count) as total_hits
            FROM identity_cache
        """,
            (datetime.now(),),
        )

        identity_stats = cursor.fetchone()

        # Most accessed endpoints
        cursor.execute(
            """
            SELECT endpoint, SUM(hit_count) as hits
            FROM api_cache
            GROUP BY endpoint
            ORDER BY hits DESC
            LIMIT 5
        """
        )

        top_endpoints = cursor.fetchall()

        conn.close()

        return {
            "api_cache": {
                "total_entries": api_stats[0],
                "valid_entries": api_stats[1],
                "expired_entries": api_stats[2],
                "total_hits": api_stats[3] or 0,
                "avg_hits_per_entry": round(api_stats[4], 2) if api_stats[4] else 0,
            },
            "identity_cache": {
                "total_entries": identity_stats[0],
                "valid_entries": identity_stats[1],
                "total_hits": identity_stats[2] or 0,
            },
            "top_endpoints": [
                {"endpoint": ep, "hits": hits} for ep, hits in top_endpoints
            ],
            "cache_file": self.db_path,
            "default_ttl_seconds": self.default_ttl,
        }

    def view_cache_contents(
        self, limit: int = 50, include_expired: bool = False
    ) -> Dict[str, Any]:
        """
        View the actual contents of the cache.

        Args:
            limit: Maximum number of entries to return per cache type (default: 50)
            include_expired: Whether to include expired entries (default: False)

        Returns:
            Dict containing cache entries with details
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now()

        # Build WHERE clause based on include_expired
        where_clause = "" if include_expired else "WHERE expires_at > ?"
        params = [] if include_expired else [now]

        # Get API cache entries
        cursor.execute(
            f"""
            SELECT
                endpoint,
                query_params,
                created_at,
                expires_at,
                hit_count,
                last_accessed,
                CASE WHEN expires_at > ? THEN 'valid' ELSE 'expired' END as status
            FROM api_cache
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """,
            [now] + params + [limit],
        )

        api_entries = []
        for row in cursor.fetchall():
            (
                endpoint,
                query_params,
                created_at,
                expires_at,
                hit_count,
                last_accessed,
                status,
            ) = row
            created_dt = datetime.fromisoformat(created_at)
            expires_dt = datetime.fromisoformat(expires_at)
            age_seconds = (now - created_dt).total_seconds()
            ttl_remaining = (expires_dt - now).total_seconds()

            # Parse query params to show summary
            try:
                params_dict = json.loads(query_params)
                # Truncate long params for readability
                params_summary = (
                    str(params_dict)[:100] + "..."
                    if len(str(params_dict)) > 100
                    else str(params_dict)
                )
            except:
                params_summary = query_params[:100]

            api_entries.append(
                {
                    "endpoint": endpoint,
                    "params_summary": params_summary,
                    "status": status,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "age_seconds": round(age_seconds, 1),
                    "ttl_remaining_seconds": round(ttl_remaining, 1),
                    "hit_count": hit_count,
                    "last_accessed": last_accessed,
                }
            )

        # Get identity cache entries
        cursor.execute(
            f"""
            SELECT
                email,
                display_name,
                identity_id,
                created_at,
                expires_at,
                hit_count,
                CASE WHEN expires_at > ? THEN 'valid' ELSE 'expired' END as status
            FROM identity_cache
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """,
            [now] + params + [limit],
        )

        identity_entries = []
        for row in cursor.fetchall():
            (
                email,
                display_name,
                identity_id,
                created_at,
                expires_at,
                hit_count,
                status,
            ) = row
            created_dt = datetime.fromisoformat(created_at)
            expires_dt = datetime.fromisoformat(expires_at)
            age_seconds = (now - created_dt).total_seconds()
            ttl_remaining = (expires_dt - now).total_seconds()

            identity_entries.append(
                {
                    "email": email,
                    "display_name": display_name,
                    "identity_id": identity_id,
                    "status": status,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "age_seconds": round(age_seconds, 1),
                    "ttl_remaining_seconds": round(ttl_remaining, 1),
                    "hit_count": hit_count,
                }
            )

        conn.close()

        logger.info(
            f"📋 Cache contents viewed - {len(api_entries)} API entries, {len(identity_entries)} identity entries"
        )

        return {
            "api_cache_entries": api_entries,
            "identity_cache_entries": identity_entries,
            "total_shown": {
                "api_cache": len(api_entries),
                "identity_cache": len(identity_entries),
            },
            "limit": limit,
            "include_expired": include_expired,
            "timestamp": now.isoformat(),
        }

    def get_cache_efficiency(self) -> Dict[str, Any]:
        """
        Calculate cache efficiency metrics.

        Returns:
            Dict with detailed efficiency metrics including hit rate, miss rate, etc.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now()

        # Get API cache efficiency metrics
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_entries,
                COUNT(CASE WHEN expires_at > ? THEN 1 END) as valid_entries,
                SUM(hit_count) as total_hits,
                SUM(CASE WHEN hit_count = 0 THEN 1 ELSE 0 END) as unused_entries,
                SUM(CASE WHEN hit_count > 0 AND expires_at > ? THEN 1 ELSE 0 END) as utilized_entries,
                MAX(hit_count) as max_hits,
                AVG(hit_count) as avg_hits
            FROM api_cache
        """,
            (now, now),
        )

        api_metrics = cursor.fetchone()
        (
            total_entries,
            valid_entries,
            total_hits,
            unused_entries,
            utilized_entries,
            max_hits,
            avg_hits,
        ) = api_metrics

        # Get identity cache efficiency
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_entries,
                COUNT(CASE WHEN expires_at > ? THEN 1 END) as valid_entries,
                SUM(hit_count) as total_hits,
                SUM(CASE WHEN hit_count = 0 THEN 1 ELSE 0 END) as unused_entries,
                MAX(hit_count) as max_hits,
                AVG(hit_count) as avg_hits
            FROM identity_cache
        """,
            (now,),
        )

        identity_metrics = cursor.fetchone()
        id_total, id_valid, id_hits, id_unused, id_max_hits, id_avg_hits = (
            identity_metrics
        )

        # Calculate total requests (hits + misses)
        # Note: We can't track misses directly, but we can estimate based on entries with 0 hits
        total_api_requests = (total_hits or 0) + (unused_entries or 0)
        total_identity_requests = (id_hits or 0) + (id_unused or 0)

        # Calculate hit rates
        api_hit_rate = (
            (total_hits / total_api_requests * 100) if total_api_requests > 0 else 0
        )
        identity_hit_rate = (
            (id_hits / total_identity_requests * 100)
            if total_identity_requests > 0
            else 0
        )

        # Calculate utilization rate (percentage of cache entries that have been accessed)
        api_utilization = (
            (utilized_entries / valid_entries * 100) if valid_entries > 0 else 0
        )

        # Get cache size information
        cursor.execute(
            "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()"
        )
        db_size_bytes = cursor.fetchone()[0]
        db_size_mb = db_size_bytes / (1024 * 1024)

        # Get most and least accessed entries
        cursor.execute(
            """
            SELECT endpoint, hit_count
            FROM api_cache
            WHERE expires_at > ?
            ORDER BY hit_count DESC
            LIMIT 5
        """,
            (now,),
        )
        most_accessed = [
            {"endpoint": ep, "hits": hits} for ep, hits in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT endpoint, hit_count
            FROM api_cache
            WHERE expires_at > ? AND hit_count > 0
            ORDER BY hit_count ASC
            LIMIT 5
        """,
            (now,),
        )
        least_accessed = [
            {"endpoint": ep, "hits": hits} for ep, hits in cursor.fetchall()
        ]

        conn.close()

        logger.info(
            f"📊 Cache efficiency calculated - API hit rate: {api_hit_rate:.1f}%, Identity hit rate: {identity_hit_rate:.1f}%"
        )

        return {
            "overall_efficiency": {
                "api_cache_hit_rate_percent": round(api_hit_rate, 2),
                "identity_cache_hit_rate_percent": round(identity_hit_rate, 2),
                "combined_hit_rate_percent": round(
                    ((total_hits or 0) + (id_hits or 0))
                    / max(1, (total_api_requests + total_identity_requests))
                    * 100,
                    2,
                ),
                "cache_utilization_percent": round(api_utilization, 2),
            },
            "api_cache_metrics": {
                "total_entries": total_entries,
                "valid_entries": valid_entries,
                "expired_entries": total_entries - valid_entries,
                "total_hits": total_hits or 0,
                "unused_entries": unused_entries or 0,
                "utilized_entries": utilized_entries or 0,
                "max_hits_single_entry": max_hits or 0,
                "avg_hits_per_entry": round(avg_hits, 2) if avg_hits else 0,
            },
            "identity_cache_metrics": {
                "total_entries": id_total,
                "valid_entries": id_valid,
                "expired_entries": id_total - id_valid,
                "total_hits": id_hits or 0,
                "unused_entries": id_unused or 0,
                "max_hits_single_entry": id_max_hits or 0,
                "avg_hits_per_entry": round(id_avg_hits, 2) if id_avg_hits else 0,
            },
            "cache_performance": {
                "most_accessed_endpoints": most_accessed,
                "least_accessed_endpoints": least_accessed,
            },
            "storage": {
                "database_size_bytes": db_size_bytes,
                "database_size_mb": round(db_size_mb, 2),
                "database_path": self.db_path,
            },
            "recommendations": self._generate_efficiency_recommendations(
                api_hit_rate, api_utilization, unused_entries, total_entries
            ),
            "timestamp": now.isoformat(),
        }

    def _generate_efficiency_recommendations(
        self, hit_rate: float, utilization: float, unused: int, total: int
    ) -> list:
        """Generate recommendations based on cache efficiency metrics."""
        recommendations = []

        if hit_rate < 30:
            recommendations.append(
                {
                    "level": "warning",
                    "message": f"Low cache hit rate ({hit_rate:.1f}%). Consider increasing TTL or reviewing cache strategy.",
                }
            )
        elif hit_rate > 80:
            recommendations.append(
                {
                    "level": "success",
                    "message": f"Excellent cache hit rate ({hit_rate:.1f}%). Cache is performing well.",
                }
            )

        if utilization < 50:
            recommendations.append(
                {
                    "level": "info",
                    "message": f"Low cache utilization ({utilization:.1f}%). Many cached items are not being reused.",
                }
            )

        if unused and unused > total * 0.3:
            recommendations.append(
                {
                    "level": "warning",
                    "message": f"{unused} entries have never been accessed. Consider reducing TTL or cache scope.",
                }
            )

        if not recommendations:
            recommendations.append(
                {
                    "level": "success",
                    "message": "Cache efficiency is good. No immediate optimizations needed.",
                }
            )

        return recommendations

    def start_auto_cleanup(self):
        """
        Start automatic background cleanup of expired cache entries.

        Runs cleanup every hour (matching the default TTL) to remove expired entries.
        This prevents the cache database from growing indefinitely.
        """
        if self._cleanup_running:
            logger.warning("Auto-cleanup already running")
            return

        self._cleanup_running = True

        def cleanup_thread():
            """Background thread that runs periodic cleanup."""
            logger.info(
                f"🔄 Auto-cleanup thread started (interval: {self.default_ttl}s)"
            )

            while self._cleanup_running:
                try:
                    # Wait for the cleanup interval (default: 1 hour)
                    time.sleep(self.default_ttl)

                    if not self._cleanup_running:
                        break

                    # Run cleanup
                    deleted_count = self.cleanup_expired()

                    if deleted_count > 0:
                        logger.info(
                            f"🧹 AUTO-CLEANUP: Removed {deleted_count} expired entries"
                        )
                    else:
                        logger.debug("🧹 AUTO-CLEANUP: No expired entries to remove")

                except Exception as e:
                    logger.error(f"❌ Error in auto-cleanup thread: {e}")

            logger.info("🛑 Auto-cleanup thread stopped")

        self._cleanup_thread = threading.Thread(
            target=cleanup_thread, daemon=True, name="CacheAutoCleanup"
        )
        self._cleanup_thread.start()

        logger.info(f"✅ Auto-cleanup enabled - will run every {self.default_ttl}s")

    def stop_auto_cleanup(self):
        """Stop the automatic cleanup thread."""
        if not self._cleanup_running:
            logger.warning("Auto-cleanup is not running")
            return

        logger.info("🛑 Stopping auto-cleanup thread...")
        self._cleanup_running = False

        # Wait for thread to finish (with timeout)
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

        logger.info("✅ Auto-cleanup stopped")

    def close(self):
        """
        Explicitly shut down the cache (stop background cleanup).

        Call this when the server is shutting down for a clean exit.
        Prefer this over relying on __del__, which is non-deterministic.
        """
        if self._cleanup_running:
            self.stop_auto_cleanup()
        logger.info("Cache closed")

    def __del__(self):
        """Best-effort cleanup on garbage collection. Prefer close() for explicit shutdown."""
        try:
            if self._cleanup_running:
                self.stop_auto_cleanup()
        except Exception:
            pass  # Suppress errors during interpreter shutdown
