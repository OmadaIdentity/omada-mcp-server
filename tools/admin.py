# tools/admin.py
"""Admin tools: ping, check_omada_config, get_cache_stats, clear_cache,
view_cache_contents_detailed, view_cache_contents, get_cache_efficiency."""
import json
import logging
import os
from datetime import datetime

import auth  # used by logout tool
from cache_instance import CACHE_ENABLED, CACHE_TTL_SECONDS, cache
from helpers import build_error_response, build_success_response
from logging_config import with_function_logging
from mcp_instance import mcp
from rate_limiter import with_rate_limit

logger = logging.getLogger("server")


@with_rate_limit
@with_function_logging
@mcp.tool()
def ping() -> str:
    logger.info("Ping function called - responding with pong")
    return "pong"


@with_rate_limit
@with_function_logging
@mcp.tool()
async def check_omada_config() -> str:
    """
    Check and display current Omada server configuration.

    Returns:
        JSON string with Omada configuration details
    """
    try:
        config = {
            "name": "Omada MCP Server",
            "version": "1.0.0",
            "omada_base_url": os.getenv("OMADA_BASE_URL", "NOT_SET"),
            "graphql_endpoint_version": os.getenv("GRAPHQL_ENDPOINT_VERSION", "3.0"),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "log_file": os.getenv("LOG_FILE", "omada_mcp_server.log"),
        }

        # Validate required settings
        missing = []
        if config["omada_base_url"] == "NOT_SET":
            missing.append("OMADA_BASE_URL")

        if missing:
            config["status"] = "INVALID"
            config["error"] = (
                f"Missing required environment variables: {', '.join(missing)}"
            )
        else:
            config["status"] = "VALID"

        # Add note about authentication
        config["note"] = (
            "Authentication is handled automatically via Auth Code + PKCE. No bearer_token or impersonate_user parameters are required."
        )
        config["usage_example"] = (
            "get_pending_approvals(workflow_step='ManagerApproval')"
        )

        return build_success_response(data=config)

    except Exception as e:
        return build_error_response(error_type=type(e).__name__, message=str(e))


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_cache_stats() -> str:
    """
    Get cache statistics and performance metrics.

    Shows:
    - Number of cached entries (total, valid, expired)
    - Cache hit counts
    - Most frequently accessed endpoints
    - Cache configuration (enabled/disabled, TTL)

    Returns:
        JSON string with cache statistics
    """
    try:
        if not CACHE_ENABLED or cache is None:
            return json.dumps(
                {
                    "cache_enabled": False,
                    "message": "Cache is disabled. Set CACHE_ENABLED=true in .env to enable caching.",
                },
                indent=2,
            )

        # Get stats from cache
        stats = cache.get_stats()

        # Clean up expired entries
        expired_count = cache.cleanup_expired()

        result = {
            "cache_enabled": True,
            "cache_statistics": stats,
            "expired_entries_cleaned": expired_count,
            "configuration": {
                "default_ttl_seconds": CACHE_TTL_SECONDS,
                "cache_file": stats.get("cache_file", "omada_cache.db"),
            },
        }

        logger.info(
            f"📊 Cache stats requested - Valid entries: {stats['api_cache']['valid_entries']}, Hits: {stats['api_cache']['total_hits']}"
        )

        return json.dumps(result, indent=2)

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__, message=f"Error getting cache stats: {str(e)}"
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def clear_cache(endpoint: str = None) -> str:
    """
    Clear cache entries.

    IMPORTANT: Use this tool when you need fresh data from the Omada API.

    Common scenarios:
    - After making changes in Omada (new assignments, approvals, etc.)
    - When cache might be stale
    - To force a fresh API call

    Args:
        endpoint: Optional specific endpoint to clear (e.g., "graphql", "identity")
                 If not provided, clears ALL cache entries

    Examples:
        clear_cache()                    # Clear entire cache
        clear_cache(endpoint="graphql")  # Clear only GraphQL cache

    Returns:
        Success message with count of cleared entries
    """
    try:
        if not CACHE_ENABLED or cache is None:
            return json.dumps(
                {
                    "cache_enabled": False,
                    "message": "Cache is disabled. No cache entries to clear.",
                },
                indent=2,
            )

        # Clear cache
        deleted_count = cache.invalidate(endpoint=endpoint)

        if endpoint:
            message = f"✅ Cache cleared for endpoint: {endpoint}"
            logger.info(
                f"🗑️ Cache cleared for endpoint '{endpoint}' - {deleted_count} entries deleted"
            )
        else:
            message = f"✅ Entire cache cleared"
            logger.info(f"🗑️ ENTIRE cache cleared - {deleted_count} entries deleted")

        return json.dumps(
            {
                "success": True,
                "message": message,
                "entries_deleted": deleted_count,
                "endpoint": endpoint or "all",
            },
            indent=2,
        )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__, message=f"Error clearing cache: {str(e)}"
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def view_cache_contents_detailed(
    limit: int = 10, include_expired: bool = False
) -> str:
    """
    View detailed cache contents including FULL parameters for debugging.

    Shows complete query parameters to help identify why duplicate entries exist.
    Useful for debugging cache key generation issues.

    Args:
        limit: Maximum number of entries to show (default: 10, lower for readability)
        include_expired: Whether to include expired entries (default: False)

    Returns:
        JSON with detailed cache entries including full query parameters
    """
    try:
        if not CACHE_ENABLED or cache is None:
            return json.dumps(
                {
                    "cache_enabled": False,
                    "message": "Cache is disabled. No cache contents to view.",
                },
                indent=2,
            )

        # Get raw cache data from database
        import sqlite3

        conn = sqlite3.connect(cache.db_path)
        cursor = conn.cursor()
        now = datetime.now()

        where_clause = "" if include_expired else "WHERE expires_at > ?"
        params = [] if include_expired else [now]

        cursor.execute(
            f"""
            SELECT
                cache_key,
                endpoint,
                query_params,
                created_at,
                expires_at,
                hit_count,
                last_accessed
            FROM api_cache
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """,
            params + [limit],
        )

        entries = []
        for row in cursor.fetchall():
            (
                cache_key,
                endpoint,
                query_params,
                created_at,
                expires_at,
                hit_count,
                last_accessed,
            ) = row
            created_dt = datetime.fromisoformat(created_at)
            expires_dt = datetime.fromisoformat(expires_at)
            age_seconds = (now - created_dt).total_seconds()
            ttl_remaining = (expires_dt - now).total_seconds()

            # Parse full query params
            try:
                params_dict = json.loads(query_params)
            except:
                params_dict = {"error": "Could not parse params", "raw": query_params}

            entries.append(
                {
                    "cache_key": cache_key,
                    "cache_key_short": cache_key[:16] + "...",
                    "endpoint": endpoint,
                    "full_params": params_dict,  # FULL PARAMETERS
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "age_seconds": round(age_seconds, 1),
                    "ttl_remaining_seconds": round(ttl_remaining, 1),
                    "hit_count": hit_count,
                    "last_accessed": last_accessed,
                    "status": "valid" if expires_dt > now else "expired",
                }
            )

        conn.close()

        result = {
            "detailed_entries": entries,
            "total_shown": len(entries),
            "limit": limit,
            "include_expired": include_expired,
            "note": "This shows FULL parameters to debug duplicate entries",
        }

        logger.info(
            f"📋 Detailed cache contents viewed - {len(entries)} entries with full params"
        )

        return json.dumps(result, indent=2)

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=f"Error viewing detailed cache contents: {str(e)}",
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def view_cache_contents(limit: int = 50, include_expired: bool = False) -> str:
    """
    View the actual contents of the cache.

    Shows what data is currently cached, including:
    - Endpoint names and parameters
    - Cache status (valid/expired)
    - Age and time remaining until expiration
    - Hit counts (how many times each entry was accessed)
    - Identity cache entries (cached users)

    This is useful for:
    - Understanding what data is cached
    - Debugging cache behavior
    - Identifying frequently accessed data
    - Checking cache freshness

    Args:
        limit: Maximum number of entries to show per cache type (default: 50)
        include_expired: Whether to include expired entries (default: False)

    Examples:
        view_cache_contents()                          # Show 50 most recent valid entries
        view_cache_contents(limit=100)                 # Show 100 entries
        view_cache_contents(include_expired=True)      # Include expired entries

    Returns:
        JSON with cache contents and details
    """
    try:
        if not CACHE_ENABLED or cache is None:
            return json.dumps(
                {
                    "cache_enabled": False,
                    "message": "Cache is disabled. No cache contents to view.",
                },
                indent=2,
            )

        # Get cache contents
        contents = cache.view_cache_contents(
            limit=limit, include_expired=include_expired
        )

        logger.info(
            f"📋 Cache contents viewed - {contents['total_shown']['api_cache']} API + {contents['total_shown']['identity_cache']} identity entries"
        )

        return json.dumps(contents, indent=2)

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=f"Error viewing cache contents: {str(e)}",
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_cache_efficiency() -> str:
    """
    Get detailed cache efficiency metrics and performance analysis.

    Provides comprehensive cache performance data including:
    - Hit rate percentage (how often cache is used vs API calls)
    - Cache utilization (percentage of cached entries being reused)
    - Most and least accessed endpoints
    - Storage usage (database size)
    - Performance recommendations

    Metrics explained:
    - Hit Rate: Percentage of requests served from cache (higher is better)
      - >80% = Excellent
      - 50-80% = Good
      - <50% = May need optimization
    - Utilization: Percentage of cache entries that are being accessed
      - High utilization = Cache is effective
      - Low utilization = Caching data that isn't needed

    Use this to:
    - Evaluate cache performance
    - Identify optimization opportunities
    - Understand access patterns
    - Tune cache TTL settings

    Returns:
        JSON with detailed efficiency metrics and recommendations
    """
    try:
        if not CACHE_ENABLED or cache is None:
            return json.dumps(
                {
                    "cache_enabled": False,
                    "message": "Cache is disabled. No efficiency metrics available.",
                },
                indent=2,
            )

        # Get efficiency metrics
        efficiency = cache.get_cache_efficiency()

        logger.info(
            f"📊 Cache efficiency: {efficiency['overall_efficiency']['combined_hit_rate_percent']:.1f}% hit rate"
        )

        return json.dumps(efficiency, indent=2)

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=f"Error calculating cache efficiency: {str(e)}",
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
def logout() -> str:
    """
    Clear the stored authentication tokens and force re-authentication on the next tool call.

    USE THIS TOOL when:
    - You want to log in as a different user
    - The token appears to be stale or corrupted
    - You need to revoke the current session

    After calling this, the next tool call will open a browser window for re-authentication.
    The encrypted token cache file (.token_cache.bin) is also deleted.

    Returns:
        Confirmation message
    """
    auth.clear_tokens()
    return build_success_response(
        data={"message": "Tokens cleared. The next tool call will require re-authentication via browser."},
    )
