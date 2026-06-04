# tools/access_requests.py
"""Access request tools: get_access_requests, create_access_request,
get_resources_for_beneficiary, get_requestable_resources,
get_identities_for_beneficiary, get_identity_contexts."""
import json
import logging

import auth
from api.graphql import _execute_graphql_request, _execute_graphql_request_cached, _summarize_graphql_data
from helpers import (
    build_error_response,
    build_pagination_clause,
    build_success_response,
    json_to_graphql_syntax,
    validate_required_fields,
)
from logging_config import with_function_logging
from mcp_instance import mcp
from rate_limiter import with_rate_limit
from tools.query import query_omada_identity

logger = logging.getLogger("server")


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_access_requests(
    filter_field: str = None,
    filter_value: str = None,
    summary_mode: bool = True,
    use_cache: bool = True,
) -> str:
    """Get access requests from Omada GraphQL API.

    Args:
        filter_field: Optional filter field name (e.g., "beneficiaryId", "identityId", "status")
        filter_value: Optional filter value
        summary_mode: If True (default), returns only key fields (id, beneficiary, resource, status)
                     If False, returns all fields
        use_cache: Whether to use cache for this request (default: True)

    Returns:
        JSON string containing access requests data
    """
    try:
        logger.debug(
            f"Getting access requests, filter: {filter_field}={filter_value if filter_field else 'none'}"
        )

        # Build filter clause conditionally
        filter_clause = (
            f"(filters: {{{filter_field}: {json.dumps(filter_value)}}})"
            if filter_field and filter_value
            else ""
        )

        # Build GraphQL query with optional filter
        query = f"""query GetAccessRequests {{
  accessRequests{filter_clause} {{
    total
    data {{
      id
      beneficiary {{
        id
        identityId
        displayName
        contexts {{
          id
        }}
      }}
      resource {{
        name
      }}
      status {{
        approvalStatus
      }}
    }}
  }}
}}"""

        # Execute GraphQL request WITH CACHING
        result = await _execute_graphql_request_cached(
            query, use_cache=use_cache
        )

        if result["success"]:
            data = result["data"]
            # Extract and format the response
            if "data" in data and "accessRequests" in data["data"]:
                access_requests_obj = data["data"]["accessRequests"]
                total = access_requests_obj.get("total", 0)
                access_requests = access_requests_obj.get("data", [])

                # Apply summarization if requested
                response_data = access_requests
                if summary_mode:
                    response_data = _summarize_graphql_data(
                        access_requests, "AccessRequest"
                    )

                return build_success_response(
                    data={"access_requests": response_data},
                    endpoint=result["endpoint"],
                    total_requests=total,
                    requests_returned=len(response_data),
                    filter_applied=(
                        f"{filter_field}={filter_value}" if filter_field else "none"
                    ),
                    summary_mode=summary_mode,
                )
            else:
                return build_error_response(
                    error_type="DataError",
                    message="No access requests data found in response",
                    raw_response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                message=f"GraphQL request failed with status {result.get('status_code', 'unknown')}",
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=f"Error getting access requests: {str(e)}",
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def create_access_request(
    reason: str,
    context: str,
    resources: str,
    valid_from: str = None,
    valid_to: str = None,
) -> str:
    """Create an access request using GraphQL mutation.

    IMPORTANT: This function requires 3 mandatory parameters. If any are missing,
    you MUST prompt the user to provide them before calling this function.
    The identity ID is automatically fetched using the authenticated user's email.

    REQUIRED PARAMETERS (prompt user if missing):
        reason: Reason for the access request (cannot be empty)
                PROMPT: "Please provide a reason for this access request"

        context: Business context ID for the access request (cannot be empty)
                IMPORTANT WORKFLOW: When the user needs to provide a context:
                1. First, call get_identity_contexts(identity_id) to retrieve available contexts
                2. Display the available contexts to the user with their displayName and type
                   (e.g., "Personal", "Finance Department")
                3. Ask the user to select a context from the displayed list
                4. Use the corresponding context "id" (GUID) value as the context parameter

                EXAMPLE:
                "I found these contexts for you:
                 1. Personal (PERSONAL)
                 2. Finance Department (ORGANIZATIONAL)

                 Which context would you like to use for this access request?"

                NOTE: The context parameter expects the internal GUID id, not the displayName.
                      You must call get_identity_contexts first to get valid context IDs.

        resources: Resources to request access for (JSON object format, cannot be empty)
                  WORKFLOW: When the user needs to provide resources:
                  1. Call get_resources_for_beneficiary(identity_id)
                     to get available resources the user can request
                  2. Display the resources with their names and systems
                  3. Ask the user to select a resource
                  4. Use the resource "id" (GUID) in JSON format: {"id": "resource-guid"}

                  PROMPT: "Please provide the resource in JSON object format like: {\"id\": \"resource-id\"}"

    Optional parameters:
        valid_from: Optional valid from date/time (ISO format)
        valid_to: Optional valid to date/time (ISO format)

    Logging:
        Log level controlled by LOG_LEVEL_create_access_request in .env file
        Falls back to global LOG_LEVEL if not set

    Returns:
        JSON string containing the created access request ID or error information
    """
    try:
        # Ensure token is valid BEFORE reading user identity.
        # get_user_identity() is fail-closed — it raises if called unauthenticated.
        await auth.ensure_authenticated()
        impersonate_user = auth.get_user_identity()

        # Validate mandatory fields using helper
        error = validate_required_fields(
            reason=reason,
            context=context,
            resources=resources,
        )
        if error:
            return error

        # Get identity ID from the authenticated user's email
        logger.debug(f"Looking up identity ID for email: {impersonate_user}")
        identity_result = await query_omada_identity(
            field_filters=[
                {"field": "EMAIL", "value": impersonate_user, "operator": "eq"}
            ],
            select_fields="UId",
            top=1,
        )

        # Parse the identity lookup result
        try:
            identity_data = json.loads(identity_result)
            if identity_data.get("status") != "success" or not identity_data.get(
                "data", {}
            ).get("value"):
                return build_error_response(
                    error_type="IdentityLookupError",
                    message=f"Could not find identity for email: {impersonate_user}",
                    lookup_result=identity_data,
                )

            identity_entity = identity_data["data"]["value"][0]
            identity_id = str(identity_entity.get("UId"))

            if not identity_id:
                return build_error_response(
                    error_type="IdentityLookupError",
                    message=f"Identity found but no ID available for email: {impersonate_user}",
                    identity_data=identity_entity,
                )

            logger.debug(f"Found identity ID: {identity_id} for {impersonate_user}")

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            return build_error_response(
                error_type="IdentityLookupParseError",
                message=f"Failed to parse identity lookup result: {str(e)}",
                raw_result=identity_result,
            )

        # Build the GraphQL mutation with template variables filled in
        valid_from_clause = f'validFrom: "{valid_from}",' if valid_from else ""
        valid_to_clause = f'validTo: "{valid_to}",' if valid_to else ""
        context_clause = f'context: "{context}",'

        # Convert resources from JSON format to GraphQL syntax
        # JSON: {"id": "123"} -> GraphQL: {id: "123"}
        try:
            resources_graphql = json_to_graphql_syntax(resources)
            logger.debug(
                f"Converted resources from JSON to GraphQL syntax: {resources} -> {resources_graphql}"
            )
        except ValueError as e:
            return build_error_response(
                error_type="ResourcesFormatError",
                message=f"Invalid resources format: {str(e)}. Expected JSON object format like: {{'id': 'resource-id'}}",
                provided_resources=resources,
            )

        mutation = f"""mutation CreateAccessRequest {{
    createAccessRequest(accessRequest: {{
        reason: "{reason}",
        {valid_from_clause}
        {valid_to_clause}
        {context_clause}
        identities: {{id: "{identity_id}"}},
        resources: {resources_graphql}
        }})
    {{
        id
        status {{
            approvalStatus
            requestAssignmentState
        }}
        resource {{
            name
            id
            system {{
                name
                id
            }}
        }}
        validFrom
        validTo
    }}
}}"""

        logger.debug(f"Prepared GraphQL mutation:\n{mutation}")

        # Execute the GraphQL mutation (use version 1.1 for access request creation)
        result = await _execute_graphql_request(
            query=mutation,
            graphql_version="1.1",
        )

        if result["success"]:
            data = result["data"]

            # Debug: Print the actual response structure
            logger.debug(
                f"GraphQL Response Data Structure: {json.dumps(data, indent=2)}"
            )

            # Check if mutation was successful and extract the created access request ID
            if "data" in data and "createAccessRequest" in data["data"]:
                create_request_response = data["data"]["createAccessRequest"]
                logger.debug(
                    f"CreateAccessRequest Response: {json.dumps(create_request_response, indent=2)}"
                )

                # Handle both single object and array responses
                if isinstance(create_request_response, list):
                    if len(create_request_response) > 0:
                        access_request_data = create_request_response[0]
                    else:
                        return build_error_response(
                            error_type="EmptyResponse",
                            message="Empty response from createAccessRequest",
                            impersonated_user=impersonate_user,
                            raw_response=data,
                        )
                else:
                    access_request_data = create_request_response

                access_request_id = access_request_data.get("id")

                return build_success_response(
                    data={
                        "access_request_details": {
                            "id": access_request_id,
                            "status": access_request_data.get("status"),
                            "resource": access_request_data.get("resource"),
                            "validFrom": access_request_data.get("validFrom"),
                            "validTo": access_request_data.get("validTo"),
                        },
                        "request_details": {
                            "reason": reason,
                            "identity_id": identity_id,
                            "identity_email": impersonate_user,
                            "resources": resources,
                            "valid_from": valid_from,
                            "valid_to": valid_to,
                            "context": context,
                        },
                    },
                    endpoint=result["endpoint"],
                    message="Access request created successfully",
                    impersonated_user=impersonate_user,
                    access_request_id=access_request_id,
                )
            elif "errors" in data:
                # Handle GraphQL errors
                logger.error(
                    f"GraphQL mutation returned errors: {json.dumps(data['errors'], indent=2)}"
                )

                return build_error_response(
                    error_type="GraphQLError",
                    message="GraphQL mutation failed",
                    impersonated_user=impersonate_user,
                    errors=data["errors"],
                    endpoint=result["endpoint"],
                )
            else:
                logger.error("Unexpected response format from access request mutation")

                return build_error_response(
                    error_type="UnexpectedResponse",
                    message="Unexpected response format",
                    impersonated_user=impersonate_user,
                    raw_response=data,
                )
        else:
            logger.error(
                f"Access request creation failed with status {result.get('status_code', 'unknown')}"
            )

            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                message=f"GraphQL request failed with status {result.get('status_code', 'unknown')}",
                impersonated_user=impersonate_user,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=f"Error creating access request: {str(e)}",
            impersonated_user=impersonate_user,
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_resources_for_beneficiary(
    identity_id: str,
    system_id: str = None,
    context_id: str = None,
    resource_name: str = None,
) -> str:
    """
    Get resources available for an ACCESS REQUEST for a specific user/identity using Omada GraphQL API.

    USE THIS FUNCTION when user asks to:
    - "list resources for access request"
    - "what resources can I request"
    - "show requestable resources"
    - "resources I can request access to"
    - "resources available for access request"

    This is the CORRECT function for access request workflows.
    DO NOT use query_omada_resources for access requests - it does not scope to user permissions.

    CRITICAL - Identity ID Field Name:
        WRONG: Do NOT use the "Id" field (e.g., 1006715) - this is the integer database ID
        CORRECT: Use the "UId" field (e.g., "e3e869c4-369a-476e-a969-d57059d0b1e4") - this is the 32-character UUID

        When querying for identity data, you MUST:
        1. Query the Identity entity to get the user record
        2. Extract the "UId" field (NOT "Id") from the result
        3. Use that UId value as the identity_id parameter

        Example workflow:
        - Query: query_omada_identity with EMAIL filter returns {"UId": "e3e869c4-...", "Id": 1006715}
        - Use UId: "e3e869c4-..." as identity_id parameter (32 character UUID)
        - DO NOT use Id: 1006715 (this will fail!)

    IMPORTANT: This function requires 1 mandatory parameter. If missing,
    you MUST prompt the user to provide it before calling this function.

    REQUIRED PARAMETERS (prompt user if missing):
        identity_id: The identity UId (32-character UUID, NOT the integer Id field!)
                    Example: "e3e869c4-369a-476e-a969-d57059d0b1e4" (CORRECT)
                    NOT: 1006715 (WRONG - this is the Id field, not UId)
                    PROMPT: "Please provide the identity UId (32-character UUID from the UId field, not the Id field)"

    Optional parameters:
        system_id: System ID to filter resources by (e.g., "1c2768e9-86fd-43fd-9e0d-5c8fee21b59b")
        context_id: Context ID to filter resources by (e.g., "6dd03400-ddb5-4cc4-bfff-490d94b195a9")
        resource_name: Resource name to filter by (string, partial match supported)
                      Example: "Sales" will match "Sales Team Access", "Sales Reports", etc.

    Returns:
        JSON response with resources data or error message
    """
    try:
        await auth.ensure_authenticated()
        impersonate_user = auth.get_user_identity()

        # Validate mandatory fields using helper
        error = validate_required_fields(identity_id=identity_id)
        if error:
            return error

        # Validate that identity_id is a UUID (32 characters), not an integer Id
        if identity_id.strip().isdigit():
            return build_error_response(
                error_type="ValidationError",
                message=f"Invalid identity_id: '{identity_id}' appears to be an integer Id field, but this function requires the UId field (32-character UUID). "
                f"When you query an Identity, you get both 'Id' (integer like 1006715) and 'UId' (UUID like 'e3e869c4-369a-476e-a969-d57059d0b1e4'). "
                f"You MUST use the UId field, not the Id field.",
                hint="Query the identity first, then extract the 'UId' field (not 'Id') from the response",
            )

        # Build the filters object dynamically based on provided parameters
        filters = f'beneficiaryIds: "{identity_id}"'

        if system_id and system_id.strip():
            filters += f', systemId: "{system_id}"'

        if context_id and context_id.strip():
            filters += f', contextId: "{context_id}"'

        # Add resource_name filter if provided (validates and adds to GraphQL filter)
        if resource_name and resource_name.strip():
            # Escape any quotes in the resource name to prevent GraphQL injection
            escaped_resource_name = resource_name.strip().replace('"', '\\"')
            filters += f', name: "{escaped_resource_name}"'
            logger.debug(f"Added resource_name filter: {escaped_resource_name}")

        # Build GraphQL query with the filters
        query = f"""query GetResourcesForBeneficiary {{
  accessRequestComponents {{
    resources(
      filters: {{{filters}}}
    ) {{
      data {{
        name
        id
        description
        system {{
          name
          id
        }}
        resourceType {{
          name
          id
        }}
      }}
    }}
  }}
}}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with caching support
        result = await _execute_graphql_request_cached(
            query, use_cache=True
        )

        if result["success"]:
            data = result["data"]
            # Extract resources from the GraphQL response
            if "data" in data and "accessRequestComponents" in data["data"]:
                access_request_components = data["data"]["accessRequestComponents"]
                resources = access_request_components.get("resources", {}).get(
                    "data", []
                )

                return build_success_response(
                    data=resources,
                    endpoint=result["endpoint"],
                    beneficiary_id=identity_id,
                    impersonated_user=impersonate_user,
                    system_id=system_id,
                    context_id=context_id,
                    resources_count=len(resources),
                    resources=resources,
                )
            else:
                return build_error_response(
                    error_type="NoResourcesFound",
                    message="No resources found in response",
                    beneficiary_id=identity_id,
                    impersonated_user=impersonate_user,
                    response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                beneficiary_id=identity_id,
                impersonated_user=impersonate_user,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            beneficiary_id=identity_id,
            impersonated_user=impersonate_user,
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_requestable_resources(
    identity_id: str,
    system_id: str = None,
    context_id: str = None,
    resource_name: str = None,
) -> str:
    """
    Get resources that a user can request access to (alias for get_resources_for_beneficiary).

    EASY-TO-USE function for ACCESS REQUEST workflows - no need to spell "beneficiary"!

    USE THIS when user wants to:
    - List resources they can request
    - Find what resources are available for access request
    - See requestable resources for a user

    CRITICAL - Identity ID Field Name:
        WRONG: Do NOT use the "Id" field (e.g., 1006715) - this is the integer database ID
        CORRECT: Use the "UId" field (e.g., "2c68e1df-1335-4e8c-8ef9-eff1d2005629") - this is the 32-character UUID

        When you query an Identity record, it returns BOTH fields:
        - "Id": 1006715          <- WRONG - Do not use this!
        - "UId": "2c68e1df-..."  <- CORRECT - Use this as identity_id!

        YOU MUST extract the "UId" field (32-character UUID), NOT the "Id" field (integer).

    REQUIRED:
        identity_id: The user's identity UId (32-character UUID from the "UId" field, NOT the "Id" field!)
                    CORRECT example: "2c68e1df-1335-4e8c-8ef9-eff1d2005629" (UId field)
                    WRONG example: 1006715 (Id field - this will fail!)

    OPTIONAL:
        system_id: Filter by specific system
        context_id: Filter by specific context
        resource_name: Resource name to filter by (string, partial match supported)
                      Example: "Sales" will match "Sales Team Access", "Sales Reports", etc.

    Returns:
        JSON response with list of requestable resources
    """
    # This is just an alias that calls the main function
    return await get_resources_for_beneficiary(
        identity_id=identity_id,
        system_id=system_id,
        context_id=context_id,
        resource_name=resource_name,
    )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_identities_for_beneficiary(
    page: int = None, rows: int = None
) -> str:
    """
    Get a list of identities available for access requests using Omada GraphQL API.

    USE THIS FUNCTION when user asks to:
    - "list identities for access request"
    - "show identities I can request for"
    - "get beneficiary identities"
    - "list identities available for access requests"

    This function queries the accessRequestComponents.identities endpoint to get identities
    that can be used as beneficiaries in access requests.

    Optional parameters:
        page: Page number for pagination (e.g., 1, 2, 3...)
        rows: Number of rows per page (e.g., 10, 20, 50...)

    Returns:
        JSON response with identities data including pagination metadata or error message
    """
    await auth.ensure_authenticated()
    impersonate_user = auth.get_user_identity()

    logger.debug(
        f"DEBUG: ENTRY - get_identities_for_beneficiary(page={page}, rows={rows})"
    )

    try:

        # Build pagination clause using helper
        pagination_clause = build_pagination_clause(page=page, rows=rows)

        # Build GraphQL query with pagination
        query = f"""query GetIdentitiesForBeneficiary {{
  accessRequestComponents {{
    identities(
      {pagination_clause}filters: {{}}
    ) {{
      pages
      total
      data {{
        firstName
        displayName
        identityId
        id
        lastName
        contexts {{
          id
          displayName
        }}
      }}
    }}
  }}
}}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with caching support
        result = await _execute_graphql_request_cached(
            query, use_cache=True
        )

        if result["success"]:
            data = result["data"]
            # Extract identities from the GraphQL response
            if "data" in data and "accessRequestComponents" in data["data"]:
                access_request_components = data["data"]["accessRequestComponents"]
                identities_obj = access_request_components.get("identities", {})
                identities = identities_obj.get("data", [])
                total = identities_obj.get("total", len(identities))
                pages = identities_obj.get("pages", 1)

                return build_success_response(
                    data=identities,
                    endpoint=result["endpoint"],
                    impersonated_user=impersonate_user,
                    pagination={
                        "current_page": page,
                        "rows_per_page": rows,
                        "total_identities": total,
                        "total_pages": pages,
                    },
                    identities_count=len(identities),
                    identities=identities,
                )
            else:
                return build_error_response(
                    error_type="NoIdentitiesFound",
                    message="No identities found in response",
                    impersonated_user=impersonate_user,
                    response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                impersonated_user=impersonate_user,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            impersonated_user=impersonate_user,
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_identity_contexts(
    identity_id: str,
) -> str:
    """
    Get contexts for a specific identity using Omada GraphQL API.

    IMPORTANT: This function requires 1 mandatory parameter. If missing,
    you MUST prompt the user to provide it before calling this function.

    REQUIRED PARAMETERS (prompt user if missing):
        identity_id: The identity ID to get contexts for (e.g., "e3e869c4-369a-476e-a969-d57059d0b1e4")
                    PROMPT: "Please provide the identity ID"

    Returns:
        JSON response with contexts data including:
        - id: Internal GUID for the context (use this for subsequent GraphQL calls)
        - displayName: Human-readable name of the context
        - type: Context type

    IMPORTANT LLM INSTRUCTIONS - Context ID Handling:
        When presenting results to the user:
        1. DO NOT display the "id" field in your response to the user
        2. ONLY show "displayName" and "type" fields to the user
        3. INTERNALLY store/remember the "id" value for each context
        4. When the user selects a context by its displayName, USE the corresponding "id" value
           for any subsequent API calls that require a context_id parameter
        5. The "id" field is a technical GUID required by other GraphQL operations but not
           useful for end users to see

        Example user presentation:
        "Available contexts:
        - Personal (type: PERSONAL)
        - Finance Department (type: ORGANIZATIONAL)"

        But internally remember:
        - Personal: id="a1b2c3d4-..."
        - Finance Department: id="e5f6g7h8-..."
    """
    await auth.ensure_authenticated()
    impersonate_user = auth.get_user_identity()

    logger.debug(
        f"DEBUG: ENTRY - get_identity_contexts(identity_id={identity_id})"
    )

    # Validate mandatory fields
    error = validate_required_fields(identity_id=identity_id)
    if error:
        return error

    try:
        logger.debug(
            f"get_identity_contexts called with identity_id={identity_id}"
        )
        logger.debug(
            f"Validation passed, building GraphQL query for identity_id: {identity_id}"
        )

        # Build GraphQL query with the provided identity_id
        query = f"""query GetContextsForIdentity {{
  accessRequestComponents {{
    contexts(identityIds: "{identity_id}") {{
      id
      displayName
      type
    }}
  }}
}}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with caching support
        result = await _execute_graphql_request_cached(
            query, use_cache=True
        )

        if result["success"]:
            data = result["data"]
            # Extract contexts from the GraphQL response
            if "data" in data and "accessRequestComponents" in data["data"]:
                access_request_components = data["data"]["accessRequestComponents"]
                contexts = access_request_components.get("contexts", [])

                return build_success_response(
                    data=contexts,
                    endpoint=result["endpoint"],
                    identity_id=identity_id,
                    impersonated_user=impersonate_user,
                    contexts_count=len(contexts),
                    contexts=contexts,
                )
            else:
                return build_error_response(
                    error_type="NoDataFound",
                    message="No contexts found in response",
                    identity_id=identity_id,
                    impersonated_user=impersonate_user,
                    response=data,
                )
        else:
            # Handle GraphQL request failure
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                identity_id=identity_id,
                impersonated_user=impersonate_user,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            identity_id=identity_id,
            impersonated_user=impersonate_user,
        )
