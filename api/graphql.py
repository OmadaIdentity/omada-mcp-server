# api/graphql.py
"""GraphQL client helper functions."""
import asyncio
import json
import logging
import os

import httpx

import auth
from api.odata import _get_omada_base_url
from cache_config import get_ttl_for_operation
from cache_instance import CACHE_ENABLED, cache
from logging_config import with_function_logging

logger = logging.getLogger("server")

# ---------------------------------------------------------------------------
# Persistent HTTP client — reuses TCP connections and TLS sessions across
# all requests instead of doing a full handshake on every tool call.
# Lazy-initialised on first use so it is created inside the running event loop.
# ---------------------------------------------------------------------------
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return (or create) the shared async HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=60,
            ),
        )
    return _http_client


# CACHE_ENABLED and cache are imported from cache_instance (shared singleton)

# ---------------------------------------------------------------------------
# Retry configuration — tunable via environment variables
# HTTP_RETRY_MAX=1 effectively disables retries.
# ---------------------------------------------------------------------------
_RETRY_MAX: int = int(os.getenv("HTTP_RETRY_MAX", "3"))
_RETRY_BACKOFF: float = float(os.getenv("HTTP_RETRY_BACKOFF", "1.0"))


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    """
    POST request with exponential-backoff retry on transient failures.

    Retries on:
    - Network/connection errors (httpx.NetworkError, httpx.TimeoutException)
    - 429 Too Many Requests (honours Retry-After header)
    - 503 Service Unavailable

    Raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            response = await client.post(url, **kwargs)
            if response.status_code in (429, 503) and attempt < _RETRY_MAX - 1:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else _RETRY_BACKOFF * (2 ** attempt)
                logger.warning(
                    f"HTTP {response.status_code} — retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{_RETRY_MAX})"
                )
                await asyncio.sleep(wait)
                continue
            return response
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < _RETRY_MAX - 1:
                wait = _RETRY_BACKOFF * (2 ** attempt)
                logger.warning(
                    f"Network error on attempt {attempt + 1}/{_RETRY_MAX}: {exc} "
                    f"— retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
    if last_exc:
        raise last_exc
    raise httpx.NetworkError("Max retries exceeded")


def _summarize_graphql_data(data: list, data_type: str) -> list:
    """
    Create a summarized version of GraphQL response data with only key fields.

    Args:
        data: List of GraphQL response objects
        data_type: Type of data being summarized (e.g., "PendingApproval", "AccessRequest")

    Returns:
        Summarized data with key fields only
    """
    if not data or not isinstance(data, list):
        return data

    # Define key fields for each GraphQL data type
    summary_fields = {
        "PendingApproval": ["workflowStep", "workflowStepTitle", "reason", "resourceAssignment"],
        "AccessRequest": ["id", "beneficiary", "resource", "status"],
        "CalculatedAssignment": ["complianceStatus", "account", "resource", "identity"],
        "Context": ["id", "displayName", "type"],
        "Resource": ["id", "name", "description", "system"],
    }

    # Fields to explicitly exclude (technical fields users shouldn't see)
    exclude_fields = {
        "PendingApproval": ["surveyId", "surveyObjectKey", "history"],
        "AccessRequest": [],
        "CalculatedAssignment": [],
        "Context": [],
        "Resource": [],
    }

    fields_to_keep = summary_fields.get(data_type, ["id", "name"])
    fields_to_exclude = exclude_fields.get(data_type, [])

    summarized = []
    for item in data:
        summary = {}
        for field in fields_to_keep:
            if field in item:
                value = item[field]
                if isinstance(value, str) and len(value) > 100:
                    summary[field] = value[:97] + "..."
                else:
                    summary[field] = value

        # Don't auto-add id for PendingApproval
        if data_type != "PendingApproval":
            if "id" in item and "id" not in summary and "id" not in fields_to_exclude:
                summary["id"] = item["id"]

        summarized.append(summary)

    return summarized


async def _prepare_graphql_request(graphql_version: str = None):
    """
    Prepare common GraphQL request components (URL, headers, token).

    Args:
        graphql_version: GraphQL API version (defaults to GRAPHQL_ENDPOINT_VERSION env var or "3.0")

    Returns:
        tuple: (graphql_url, headers, user_identity)
    """
    token = await auth.ensure_authenticated()
    user_identity = auth.get_user_identity()
    logger.debug(f"Authenticated as {user_identity} for GraphQL request")

    omada_base_url = _get_omada_base_url()

    if not graphql_version:
        graphql_version = os.getenv("GRAPHQL_ENDPOINT_VERSION", "3.0")
    graphql_url = f"{omada_base_url}/api/Domain/{graphql_version}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": os.getenv("HTTP_USER_AGENT", "omada-mcp-server/1.0"),
    }
    impersonate_env = os.getenv("IMPERSONATE_USER")
    if impersonate_env:
        headers["impersonate_user"] = impersonate_env

    return graphql_url, headers, user_identity


async def _execute_graphql_request_cached(
    query: str,
    variables: dict = None,
    graphql_version: str = None,
    use_cache: bool = True,
) -> dict:
    """
    Execute a GraphQL request with caching support.

    Currently passes through directly to _execute_graphql_request.
    Cache infrastructure (cache.py) is ready — set CACHE_ENABLED=true to activate.

    Args:
        query: GraphQL query string
        variables: Optional GraphQL variables
        graphql_version: GraphQL API version to use
        use_cache: Whether to use cache (default: True, no-op while disabled)

    Returns:
        dict: Parsed response dict
    """
    is_mutation = "mutation" in query.lower()

    if CACHE_ENABLED and cache is not None and use_cache and not is_mutation:
        # try_get_user_identity() is safe here — auth may not have run yet.
        # If it returns None the cache key still works (None serialises consistently).
        user_identity = auth.try_get_user_identity()
        cache_params = {
            "query": query,
            "variables": variables or {},
            "version": graphql_version or "3.0",
            "user_identity": user_identity,
        }
        cached_result = cache.get("graphql", cache_params)
        if cached_result:
            return cached_result

    result = await _execute_graphql_request(query, variables, graphql_version)

    if CACHE_ENABLED and cache is not None and use_cache and not is_mutation and result.get("success"):
        ttl = get_ttl_for_operation(query, is_mutation)
        if ttl > 0:
            # After _execute_graphql_request, auth is complete — get_user_identity() is safe.
            user_identity = auth.try_get_user_identity()
            cache_params = {
                "query": query,
                "variables": variables or {},
                "version": graphql_version or "3.0",
                "user_identity": user_identity,
            }
            cache.set("graphql", cache_params, result, ttl_seconds=ttl)

    return result


@with_function_logging
async def _execute_graphql_request(
    query: str,
    variables: dict = None,
    graphql_version: str = None,
) -> dict:
    """
    Execute a GraphQL request using the shared persistent HTTP client.

    Args:
        query: GraphQL query string
        variables: Optional GraphQL variables
        graphql_version: GraphQL API version to use

    Returns:
        dict: {"success": bool, "data": ..., "status_code": int, "endpoint": str}
              Errors add "error" key. Never includes request headers or raw bodies.
    """
    try:
        graphql_url, headers, user_identity = await _prepare_graphql_request(graphql_version)

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        logger.debug(f"GraphQL Request to {graphql_url} as {user_identity}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Query:\n{query}")
            if variables:
                logger.debug(f"Variables: {json.dumps(variables, indent=2)}")

        client = _get_http_client()
        response = await _post_with_retry(client, graphql_url, json=payload, headers=headers)

        logger.debug(f"GraphQL Response status: {response.status_code}")

        result = {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "endpoint": graphql_url,
        }

        if response.status_code == 200:
            # Parse JSON once — reuse the parsed object for both logging and result
            data = response.json()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Response data: {json.dumps(data, indent=2)}")
            result["data"] = data
        else:
            error_text = response.text
            logger.debug(f"GraphQL error body: {error_text}")
            result["error"] = error_text

        return result

    except Exception as e:
        return {"success": False, "error": str(e), "error_type": type(e).__name__}
