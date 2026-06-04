# rate_limiter.py
"""
Token-bucket rate limiter for MCP tool calls.

Protects the Omada backend from being hammered by a loop or rapid-fire
tool invocations.  Each tool shares a single global bucket unless isolated
with a per-tool override (RATE_LIMIT_<tool_name>=N).

Configuration (via .env):
    RATE_LIMIT_CALLS   int    Max calls allowed per window (default: 30)
    RATE_LIMIT_WINDOW  float  Rolling window in seconds       (default: 60)

Setting RATE_LIMIT_CALLS=0 disables rate limiting entirely.

Per-tool overrides follow the same LOG_LEVEL pattern:
    RATE_LIMIT_get_pending_approvals=10
"""

import asyncio
import logging
import os
import time
from collections import deque
from functools import wraps

logger = logging.getLogger("server")

# ---------------------------------------------------------------------------
# Global defaults (read once at import time; env vars take effect immediately
# after a server restart).
# ---------------------------------------------------------------------------
_DEFAULT_CALLS: int = int(os.getenv("RATE_LIMIT_CALLS", "30"))
_DEFAULT_WINDOW: float = float(os.getenv("RATE_LIMIT_WINDOW", "60"))

# Shared bucket: maps bucket_name → deque of call timestamps
_buckets: dict[str, deque] = {}
_bucket_lock = asyncio.Lock()


def _get_limit(tool_name: str) -> tuple[int, float]:
    """Return (max_calls, window_seconds) for a given tool."""
    override = os.getenv(f"RATE_LIMIT_{tool_name}")
    max_calls = int(override) if override is not None else _DEFAULT_CALLS
    return max_calls, _DEFAULT_WINDOW


async def _check_rate_limit(tool_name: str) -> None:
    """
    Raise RateLimitError if the tool has exceeded its call budget.

    Uses a sliding-window counter: calls older than `window` seconds are
    evicted, then the current call is admitted or rejected.

    Raises:
        RateLimitError: When the call budget for the window is exhausted.
    """
    max_calls, window = _get_limit(tool_name)

    if max_calls == 0:
        return  # Rate limiting disabled

    async with _bucket_lock:
        now = time.monotonic()
        bucket = _buckets.setdefault(tool_name, deque())

        # Evict timestamps outside the current window
        while bucket and now - bucket[0] > window:
            bucket.popleft()

        if len(bucket) >= max_calls:
            oldest = bucket[0]
            retry_in = window - (now - oldest)
            raise RateLimitError(
                f"Rate limit exceeded for '{tool_name}': "
                f"{max_calls} calls per {window:.0f}s. "
                f"Retry in {retry_in:.1f}s."
            )

        bucket.append(now)


class RateLimitError(Exception):
    """Raised when a tool call exceeds the configured rate limit."""
    pass


def with_rate_limit(func):
    """
    Decorator that enforces a sliding-window rate limit on a tool.

    Wrap AFTER @mcp.tool() and @with_function_logging so the tool is
    registered correctly but rate-checked before execution:

        @with_rate_limit
        @with_function_logging
        @mcp.tool()
        async def my_tool(): ...

    Returns a JSON error string (not an exception) on limit exceeded, so
    the MCP client receives a clean structured response rather than a crash.
    """
    import json  # local import to avoid circular deps

    if asyncio.iscoroutinefunction(func):
        async def async_wrapper(*args, **kwargs):
            try:
                await _check_rate_limit(func.__name__)
            except RateLimitError as e:
                logger.warning(f"Rate limit hit: {func.__name__} — {e}")
                return json.dumps({
                    "status": "error",
                    "error_type": "RateLimitError",
                    "message": str(e),
                })
            return await func(*args, **kwargs)

        async_wrapper.__name__ = func.__name__
        async_wrapper.__doc__ = func.__doc__
        async_wrapper.__module__ = func.__module__
        async_wrapper.__qualname__ = func.__qualname__
        async_wrapper.__annotations__ = func.__annotations__
        async_wrapper.__dict__.update(func.__dict__)
        return async_wrapper

    else:
        # Sync tools: can't await, so we use a best-effort sync check
        def sync_wrapper(*args, **kwargs):
            import threading
            max_calls, window = _get_limit(func.__name__)
            if max_calls == 0:
                return func(*args, **kwargs)
            # Simple sync fallback — not perfectly accurate under concurrency
            # but sync MCP tools are rare and never called in tight loops
            now = time.monotonic()
            bucket = _buckets.setdefault(func.__name__, deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= max_calls:
                oldest = bucket[0]
                retry_in = window - (now - oldest)
                import json
                logger.warning(f"Rate limit hit (sync): {func.__name__}")
                return json.dumps({
                    "status": "error",
                    "error_type": "RateLimitError",
                    "message": (
                        f"Rate limit exceeded for '{func.__name__}': "
                        f"{max_calls} calls per {window:.0f}s. "
                        f"Retry in {retry_in:.1f}s."
                    ),
                })
            bucket.append(now)
            return func(*args, **kwargs)

        sync_wrapper.__name__ = func.__name__
        sync_wrapper.__doc__ = func.__doc__
        sync_wrapper.__module__ = func.__module__
        sync_wrapper.__qualname__ = func.__qualname__
        sync_wrapper.__annotations__ = func.__annotations__
        sync_wrapper.__dict__.update(func.__dict__)
        return sync_wrapper
