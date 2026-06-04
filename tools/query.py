# tools/query.py
"""OData query tools: query_omada_entity, query_omada_identity, query_omada_resources,
query_omada_entities, query_calculated_assignments, get_all_omada_identities."""
import asyncio
import logging
import os
import urllib.parse

import httpx

import auth
from api.odata import _build_odata_filter, _get_omada_base_url, _summarize_entities
from exceptions import AuthenticationError, AuthorizationError, ODataQueryError, OmadaServerError
from helpers import build_error_response, build_success_response
from logging_config import with_function_logging
from mcp_instance import mcp
from rate_limiter import with_rate_limit

logger = logging.getLogger("server")

# Persistent HTTP client — reuses TCP/TLS connections across all OData requests.
# Lazy-initialised on first use so it is created inside the running event loop.
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


# Retry configuration — mirrors graphql.py; tunable via the same env vars.
_RETRY_MAX: int = int(os.getenv("HTTP_RETRY_MAX", "3"))
_RETRY_BACKOFF: float = float(os.getenv("HTTP_RETRY_BACKOFF", "1.0"))


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    """
    GET request with exponential-backoff retry on transient failures.

    Retries on:
    - Network/connection errors (httpx.NetworkError, httpx.TimeoutException)
    - 429 Too Many Requests (honours Retry-After header)
    - 503 Service Unavailable

    Raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            response = await client.get(url, **kwargs)
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


@with_rate_limit
@with_function_logging
@mcp.tool()
async def query_omada_entity(
    entity_type: str = "Identity",
    filters: dict = None,
    count_only: bool = False,
    summary_mode: bool = True,
    top: int = None,
    skip: int = None,
    select_fields: str = None,
    order_by: str = None,
    expand: str = None,
    include_count: bool = False,
) -> str:
    """
    Generic query function for any Omada entity type (Identity, Resource, Role, etc).

    IMPORTANT - Key Identity Field Names (use EXACTLY as shown - all UPPERCASE):

        Core Fields:
        - EMAIL (not "email", "MAIL", or "EMAILADDRESS")
        - FIRSTNAME (not "firstname" or "first_name")
        - LASTNAME (not "lastname" or "last_name")
        - DISPLAYNAME (not "displayname" or "display_name")
        - IDENTITYID (the user's login ID, not "identity_id")
        - EMPLOYEEID (not "employee_id" or "EmployeeId")
        - JOBTITLE (not "job_title" or "JobTitle")
        - DEPARTMENT (not "department")
        - COMPANY (not "company")
        - STATUS (not "status")

        Reference Fields (for expanding related data):
        - JOBTITLE_REF (expands to full job title object with Id, DisplayName, etc.)
        - COMPANY_REF (expands to full company/organization object)
        - MANAGER_REF (expands to manager identity details)
        - DEPARTMENT_REF (expands to full department object)

        Other Important Fields:
        - UId (32-character GUID - use for identity_id in GraphQL functions)
        - Id (integer database ID - rarely used)
        - LOCATION (physical location/office)
        - COSTCENTER (cost center code)
        - TITLE (may be different from JOBTITLE in some configurations)

    Example: Querying with field and expanding reference:
        select_fields="FIRSTNAME,LASTNAME,EMAIL,JOBTITLE,IDENTITYID,UId"
        expand="JOBTITLE_REF,COMPANY_REF,MANAGER_REF"

    Args:
        entity_type: Type of entity to query (Identity, Resource, Role, Account, etc)
        filters: Dictionary containing filter criteria:
                {
                    "field_filters": [{"field": "EMAIL", "value": "user@domain.com", "operator": "eq"}],
                    "resource_type_id": 1011066,  # For Resource entities
                    "resource_type_name": "APPLICATION_ROLES",  # Alternative to resource_type_id
                    "system_id": 1011066,  # For Resource entities
                    "identity_id": 1006500,  # For CalculatedAssignments entities
                    "custom_filter": "FIRSTNAME eq 'John' and LASTNAME eq 'Doe'"  # Custom OData filter
                }
        count_only: If True, returns only the count of matching records
        summary_mode: If True, returns only key fields as a summary instead of full objects
        top: Maximum number of records to return (OData $top)
        skip: Number of records to skip (OData $skip)
        select_fields: Comma-separated list of fields to select (OData $select)
        order_by: Field(s) to order by (OData $orderby)
        expand: Comma-separated list of related entities to expand (OData $expand)
        include_count: Include total count in response (adds $count=true)

    Examples:
        # Query by email (IMPORTANT: field name is "EMAIL" not "email" or "MAIL")
        await query_omada_entity("Identity", filters={
            "field_filters": [{"field": "EMAIL", "value": "user@domain.com", "operator": "eq"}]
        })

        # Query by first name
        await query_omada_entity("Identity", filters={
            "field_filters": [{"field": "FIRSTNAME", "value": "John", "operator": "eq"}]
        })

        # Resource filtering
        await query_omada_entity("Resource", filters={
            "resource_type_id": 123,
            "system_id": 456
        })

        # Multiple filters combined
        await query_omada_entity("Identity", filters={
            "field_filters": [{"field": "DEPARTMENT", "value": "IT", "operator": "eq"}],
            "custom_filter": "STATUS eq 'ACTIVE'"
        })

        # CalculatedAssignments for specific identity
        await query_omada_entity("CalculatedAssignments", filters={
            "identity_id": 1006500
        })

        # Count only with custom filter
        await query_omada_entity("Identity",
            filters={"custom_filter": "DEPARTMENT eq 'Engineering'"},
            count_only=True
        )

    Returns:
        JSON response with entity data, count, or error message
    """
    try:
        # Validate entity type
        valid_entities = [
            "Identity",
            "Resource",
            "Role",
            "Account",
            "Application",
            "System",
            "CalculatedAssignments",
            "AssignmentPolicy",
        ]
        if entity_type not in valid_entities:
            return f"❌ Invalid entity type '{entity_type}'. Valid types: {', '.join(valid_entities)}"

        # Get base URL using helper function (reads from environment)
        try:
            omada_base_url = _get_omada_base_url()
        except Exception as e:
            return f"❌ {str(e)}"

        # Build the endpoint URL based on entity type
        if entity_type == "CalculatedAssignments":
            endpoint_url = f"{omada_base_url}/OData/BuiltIn/{entity_type}"
        else:
            endpoint_url = f"{omada_base_url}/OData/DataObjects/{entity_type}"

        # Build query parameters
        query_params = {}

        # Initialize filters dictionary if not provided
        if filters is None:
            filters = {}

        # Extract filter components from the filters dictionary
        field_filters = filters.get("field_filters", [])
        resource_type_id = filters.get("resource_type_id")
        resource_type_name = filters.get("resource_type_name")
        system_id = filters.get("system_id")
        identity_id = filters.get("identity_id")
        custom_filter = filters.get("custom_filter")

        # Handle entity-specific filtering logic
        auto_filters = []

        # For Resource entities, handle resource_type and system filtering
        if entity_type == "Resource":
            if resource_type_name and not resource_type_id:
                env_key = f"RESOURCE_TYPE_{resource_type_name.upper()}"
                resource_type_id = os.getenv(env_key)
                if not resource_type_id:
                    return f"❌ Resource type '{resource_type_name}' not found in environment variables. Check {env_key}"
                resource_type_id = int(resource_type_id)

            if resource_type_id:
                auto_filters.append(f"Systemref/Id eq {resource_type_id}")

            # Add system_id filter for querying resources by system (only if not already filtered by resource_type_id)
            if system_id and not resource_type_id:
                auto_filters.append(f"Systemref/Id eq {system_id}")

        # Handle generic field filtering for any entity type
        if field_filters:
            for field_filter in field_filters:
                if (
                    isinstance(field_filter, dict)
                    and "field" in field_filter
                    and "value" in field_filter
                ):
                    field_name = field_filter["field"]
                    field_value = field_filter["value"]
                    field_operator = field_filter.get("operator", "eq")
                    auto_filters.append(
                        _build_odata_filter(field_name, field_value, field_operator)
                    )

        # For CalculatedAssignments entities, handle identity_id filtering
        if entity_type == "CalculatedAssignments":
            if identity_id:
                auto_filters.append(f"Identity/Id eq {identity_id}")

        # Combine automatic filters with custom filter condition
        all_filters = []
        if auto_filters:
            all_filters.extend(auto_filters)
        if custom_filter:
            all_filters.append(f"({custom_filter})")

        if all_filters:
            query_params["$filter"] = " and ".join(all_filters)

        # Add count parameter if requested
        if count_only:
            query_params["$count"] = "true"
            query_params["$top"] = "0"  # Don't return actual records, just count
        else:
            # Add other OData parameters
            if top:
                query_params["$top"] = str(top)
            if skip:
                query_params["$skip"] = str(skip)
            if select_fields:
                query_params["$select"] = select_fields
            if order_by:
                query_params["$orderby"] = order_by
            if expand:
                query_params["$expand"] = expand
            if include_count:
                query_params["$count"] = "true"

        # Construct final URL with query parameters
        if query_params:
            query_string = urllib.parse.urlencode(query_params)
            endpoint_url = f"{endpoint_url}?{query_string}"

        # Obtain a valid token automatically (Auth Code + PKCE, with silent refresh)
        access_token = await auth.ensure_authenticated()
        user_identity = auth.get_user_identity()
        logger.debug(f"Authenticated as {user_identity} for OData request")

        # Make API call to Omada
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": os.getenv("HTTP_USER_AGENT", "omada-mcp-server/1.0"),
        }
        # Only add impersonation header when explicitly configured (requires membership
        # in CIAMServiceUsers / ImpersonationServiceUsers groups in Omada)
        impersonate_env = os.getenv("IMPERSONATE_USER")
        if impersonate_env:
            headers["impersonate_user"] = impersonate_env

        client = _get_http_client()
        response = await _get_with_retry(client, endpoint_url, headers=headers)

        if response.status_code == 200:
            # Parse the response
            data = response.json()

            if count_only:
                # Return just the count
                count = data.get("@odata.count", len(data.get("value", [])))
                return build_success_response(
                    data=None,
                    endpoint=endpoint_url,
                    entity_type=entity_type,
                    count=count,
                    filter=query_params.get("$filter", "none"),
                )
            else:
                # Return full data with metadata
                entities_found = len(data.get("value", []))
                total_count = data.get(
                    "@odata.count"
                )  # Available if $count=true was included

                # Apply summarization if requested
                response_data = data
                if summary_mode:
                    response_data = _summarize_entities(data, entity_type)

                # Build response with entity-specific metadata
                extra_fields = {
                    "entity_type": entity_type,
                    "entities_returned": entities_found,
                    "total_count": total_count,
                    "filter": query_params.get("$filter", "none"),
                    "summary_mode": summary_mode,
                }

                # Add entity-specific metadata
                if entity_type == "Resource" and resource_type_id:
                    extra_fields["resource_type_id"] = resource_type_id

                return build_success_response(
                    data=response_data, endpoint=endpoint_url, **extra_fields
                )
        elif response.status_code == 400:
            raise ODataQueryError(
                f"Bad request - invalid OData query: {response.text[:200]}",
                response.status_code,
            )
        elif response.status_code == 401:
            raise AuthenticationError(
                "Authentication failed - token may be expired", response.status_code
            )
        elif response.status_code == 403:
            raise AuthorizationError(
                "Access forbidden - insufficient permissions for this resource", response.status_code
            )
        elif response.status_code == 404:
            raise OmadaServerError(
                "Omada endpoint not found - check base URL", response.status_code
            )
        elif response.status_code >= 500:
            raise OmadaServerError(
                f"Omada server error: {response.status_code}",
                response.status_code,
                response.text,
            )
        else:
            raise OmadaServerError(
                f"Unexpected response: {response.status_code}",
                response.status_code,
                response.text,
            )

    except AuthenticationError as e:
        return build_error_response(error_type="AuthenticationError", message=str(e))
    except AuthorizationError as e:
        return build_error_response(error_type="AuthorizationError", message=str(e))
    except ODataQueryError as e:
        return build_error_response(error_type="ODataQueryError", message=str(e))
    except OmadaServerError as e:
        return build_error_response(error_type="OmadaServerError", message=str(e))
    except httpx.RequestError as e:
        return build_error_response(error_type="NetworkError", message=str(e))
    except Exception as e:
        return build_error_response(error_type=type(e).__name__, message=str(e))


@with_rate_limit
@with_function_logging
@mcp.tool()
async def query_omada_identity(
    field_filters: list = None,
    filter_condition: str = None,
    count_only: bool = False,
    summary_mode: bool = True,
    top: int = None,
    skip: int = None,
    select_fields: str = None,
    order_by: str = None,
    include_count: bool = False,
) -> str:
    """
    Query Omada Identity entities (wrapper for query_omada_entity).

    IMPORTANT LLM INSTRUCTIONS - UId Field Handling:
        When summary_mode=True (default), the response includes the UId field:
        1. DO NOT display the "UId" field to the user in your responses
        2. ONLY show user-friendly fields: DISPLAYNAME, FIRSTNAME, LASTNAME, EMAIL, EMPLOYEEID, DEPARTMENT, STATUS
        3. INTERNALLY store/remember the "UId" value for each identity
        4. When the user selects an identity by name/email, USE the corresponding "UId" value
           for any subsequent API calls that require an identity_id or identity_ids parameter
        5. The "UId" is a 32-character GUID (e.g., "2c68e1df-1335-4e8c-8ef9-eff1d2005629") required
           by GraphQL functions but not useful for end users to see

        Example user presentation:
        "Found identity: Robert Wolf (ROBWOL@domain.com)
        Department: Finance, Employee ID: EMP12345"

        But internally remember:
        - Robert Wolf: UId="2c68e1df-1335-4e8c-8ef9-eff1d2005629"

        When user says "Get assignments for Robert Wolf", use UId="2c68e1df-1335-4e8c-8ef9-eff1d2005629"
        in the identity_ids parameter.

    IMPORTANT - Identity Field Names (use EXACTLY as shown):
        - EMAIL (not "email", "MAIL", or "EMAILADDRESS")
        - FIRSTNAME (not "firstname" or "first_name")
        - LASTNAME (not "lastname" or "last_name")
        - DISPLAYNAME, EMPLOYEEID, DEPARTMENT, STATUS

    Args:
        field_filters: List of field filters:
                      [{"field": "EMAIL", "value": "user@domain.com", "operator": "eq"},
                       {"field": "FIRSTNAME", "value": "Emma", "operator": "eq"},
                       {"field": "LASTNAME", "value": "Taylor", "operator": "startswith"}]
        filter_condition: Custom OData filter condition
        count_only: If True, returns only the count
        top: Maximum number of records to return
        skip: Number of records to skip
        select_fields: Comma-separated list of fields to select
        order_by: Field(s) to order by
        include_count: Include total count in response
        summary_mode: If True (default), returns only key fields including UId (use UId internally, don't display to user)

    Returns:
        JSON response with identity data including:
        - UId: 32-character GUID (for internal use in subsequent API calls - don't display to user)
        - DISPLAYNAME, FIRSTNAME, LASTNAME, EMAIL: User-friendly display fields
        - EMPLOYEEID, DEPARTMENT, STATUS: Additional identity attributes
    """
    # Build filters dictionary for clean API
    filters = {}
    if field_filters:
        filters["field_filters"] = field_filters
    if filter_condition:
        filters["custom_filter"] = filter_condition

    return await query_omada_entity(
        entity_type="Identity",
        filters=filters if filters else None,
        count_only=count_only,
        summary_mode=summary_mode,
        top=top,
        skip=skip,
        select_fields=select_fields,
        order_by=order_by,
        include_count=include_count,
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def query_omada_resources(
    resource_type_id: int = None,
    resource_type_name: str = None,
    system_id: int = None,
    filter_condition: str = None,
    count_only: bool = False,
    top: int = None,
    skip: int = None,
    select_fields: str = None,
    order_by: str = None,
    include_count: bool = False,
) -> str:
    """
    Query Omada Resource entities using OData API (for ADMINISTRATIVE queries only).

    WARNING: DO NOT USE THIS for access request workflows!
    - This function does NOT scope resources to user permissions
    - This does NOT show what a user can request
    - For access requests, use get_requestable_resources or get_resources_for_beneficiary instead

    USE THIS FUNCTION for:
    - Administrative resource queries
    - Bulk resource reports
    - System-level resource inventory
    - Resource type analysis

    Query Omada Resource entities (wrapper for query_omada_entity).

    Args:
        resource_type_id: Numeric ID for resource type (e.g., 1011066 for Application Roles)
        resource_type_name: Name-based lookup for resource type (e.g., "APPLICATION_ROLES")
        system_id: Numeric ID for system reference to filter resources by system (e.g., 1011066)
        filter_condition: Custom OData filter condition
        count_only: If True, returns only the count
        top: Maximum number of records to return
        skip: Number of records to skip
        select_fields: Comma-separated list of fields to select
        order_by: Field(s) to order by
        include_count: Include total count in response

    Returns:
        JSON response with resource data or error message
    """
    # Build filters dictionary for clean API
    filters = {}
    if resource_type_id:
        filters["resource_type_id"] = resource_type_id
    if resource_type_name:
        filters["resource_type_name"] = resource_type_name
    if system_id:
        filters["system_id"] = system_id
    if filter_condition:
        filters["custom_filter"] = filter_condition

    return await query_omada_entity(
        entity_type="Resource",
        filters=filters if filters else None,
        count_only=count_only,
        top=top,
        skip=skip,
        select_fields=select_fields,
        order_by=order_by,
        include_count=include_count,
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def query_omada_entities(
    entity_type: str = "Identity",
    field_filters: list = None,
    filter_condition: str = None,
    count_only: bool = False,
    top: int = None,
    skip: int = None,
    select_fields: str = None,
    order_by: str = None,
    expand: str = None,
    include_count: bool = False,
) -> str:
    """
    Modern generic query function for Omada entities using field filters.

    Args:
        entity_type: Type of entity to query (Identity, Resource, System, etc)
        field_filters: List of field filters:
                      [{"field": "FIRSTNAME", "value": "Emma", "operator": "eq"},
                       {"field": "LASTNAME", "value": "Taylor", "operator": "startswith"}]
        filter_condition: Custom OData filter condition
        count_only: If True, returns only the count
        top: Maximum number of records to return
        skip: Number of records to skip
        select_fields: Comma-separated list of fields to select
        order_by: Field(s) to order by
        expand: Comma-separated list of related entities to expand
        include_count: Include total count in response

    Returns:
        JSON response with entity data or error message
    """
    # Build filters dictionary for clean API
    filters = {}
    if field_filters:
        filters["field_filters"] = field_filters
    if filter_condition:
        filters["custom_filter"] = filter_condition

    return await query_omada_entity(
        entity_type=entity_type,
        filters=filters if filters else None,
        count_only=count_only,
        top=top,
        skip=skip,
        select_fields=select_fields,
        order_by=order_by,
        expand=expand,
        include_count=include_count,
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def query_calculated_assignments(
    identity_id: int = None,
    select_fields: str = "AssignmentKey,AccountName",
    expand: str = "Identity,Resource,ResourceType",
    filter_condition: str = None,
    top: int = None,
    skip: int = None,
    order_by: str = None,
    include_count: bool = False,
) -> str:
    """
    Query Omada CalculatedAssignments entities (wrapper for query_omada_entity).

    Args:
        identity_id: Numeric ID for identity to get assignments for (e.g., 1006500)
        select_fields: Fields to select (default: "AssignmentKey,AccountName")
        expand: Related entities to expand (default: "Identity,Resource,ResourceType")
        filter_condition: Custom OData filter condition
        top: Maximum number of records to return
        skip: Number of records to skip
        order_by: Field(s) to order by
        include_count: Include total count in response

    Returns:
        JSON response with calculated assignments data or error message
    """
    # Build filters dictionary for clean API
    filters = {}
    if identity_id:
        filters["identity_id"] = identity_id
    if filter_condition:
        filters["custom_filter"] = filter_condition

    return await query_omada_entity(
        entity_type="CalculatedAssignments",
        filters=filters if filters else None,
        top=top,
        skip=skip,
        select_fields=select_fields,
        order_by=order_by,
        expand=expand,
        include_count=include_count,
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_all_omada_identities(
    top: int = 1000,
    skip: int = None,
    select_fields: str = None,
    order_by: str = None,
    include_count: bool = True,
) -> str:
    """
    Retrieve all identities from Omada Identity system with pagination support.

    Args:
        top: Maximum number of records to return (default: 1000)
        skip: Number of records to skip for pagination
        select_fields: Comma-separated list of fields to select
        order_by: Field(s) to order by
        include_count: Include total count in response

    Returns:
        JSON response with all identity data or error message
    """
    return await query_omada_identity(
        top=top,
        skip=skip,
        select_fields=select_fields,
        order_by=order_by,
        filter_condition=None,  # No filter to get all
        include_count=include_count,
    )
