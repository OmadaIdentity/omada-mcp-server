# tools/assignments.py
"""Assignment tools: get_calculated_assignments_detailed,
get_compliance_workbench_survey_and_compliance_status."""
import logging

import auth
from api.graphql import _execute_graphql_request, _execute_graphql_request_cached
from helpers import build_error_response, build_success_response, validate_required_fields
from logging_config import with_function_logging
from mcp_instance import mcp
from rate_limiter import with_rate_limit

logger = logging.getLogger("server")


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_calculated_assignments_detailed(
    identity_ids: str,
    resource_type_name: str = None,
    resource_type_operator: str = "CONTAINS",
    compliance_status: str = None,
    compliance_status_operator: str = "CONTAINS",
    account_name: str = None,
    account_name_operator: str = "CONTAINS",
    system_name: str = None,
    system_name_operator: str = "CONTAINS",
    identity_name: str = None,
    identity_name_operator: str = "CONTAINS",
    sort_by: str = "RESOURCE_NAME",
    page: int = 1,
    rows: int = 50,
    use_cache: bool = True,
) -> str:
    """
    Get detailed calculated assignments with compliance and violation status using Omada GraphQL API.

    IMPORTANT LLM INSTRUCTIONS - When to Use This Tool:
        USE THIS TOOL (GraphQL) when the user asks for assignments with ANY of these patterns:

        System Name Queries:
        - "Get me all the assignments in system {X}" (e.g., "in system AD", "in Active Directory")
        - "Show assignments for {person} in system {X}"
        - "What assignments does {person} have in {system}?"
        - "List all {system} assignments for {person}"
        - "Get assignments filtered by system name"
        - Any query that filters by system_name parameter

        Account Name Queries:
        - "Show me assignments for account {NAME}" (e.g., "for account HANULR", "for account JohnDoe")
        - "Get assignments using account {NAME}"
        - "What assignments use account {NAME}?"
        - "List assignments for account name {NAME}"
        - "Show me what {account} has access to"
        - Any query that filters by account_name parameter

        DO NOT use OData query_calculated_assignments or query_omada_entity for these requests.
        This GraphQL tool has system_name and account_name filters that are more efficient and provide richer data.

        Example user requests that MUST use this tool:
        - "Get all assignments in system AD for Robert Wolf"
        - "Show me assignments in Active Directory system"
        - "What does John have in the SAP system?"
        - "Show me assignments for account HANULR"
        - "Get assignments using account JohnDoe"
        - "What does account ROBWOL have access to?"

    IMPORTANT: This function requires 1 mandatory parameter. If missing,
    you MUST prompt the user to provide it before calling this function.

    CRITICAL - Identity ID Field Name:
        WRONG: Do NOT use the "IdentityID" field (e.g., "ROBWOL") - this is a user-readable identifier
        WRONG: Do NOT use the "Id" field (e.g., 1006715) - this is the integer database ID
        CORRECT: Use the "UId" field (e.g., "2c68e1df-1335-4e8c-8ef9-eff1d2005629") - this is the 32-character GUID

        When querying Identity data, you MUST:
        1. Query the Identity entity to get the user record
        2. Extract the "UId" field (NOT "Id" or "IdentityID") from the result
        3. Use that UId value as the identity_ids parameter

        Example workflow:
        - Query: query_omada_identity with EMAIL filter returns {"UId": "2c68e1df-...", "Id": 1006715, "IdentityID": "ROBWOL"}
        - Use UId: "2c68e1df-..." as identity_ids parameter (32 character GUID)
        - DO NOT use Id: 1006715 (this will fail!)
        - DO NOT use IdentityID: "ROBWOL" (this will fail!)

    REQUIRED PARAMETERS (prompt user if missing):
        identity_ids: One or more identity UIds (32-character GUIDs from the "UId" field, NOT the "Id" or "IdentityID" fields!)
                     Can be a single UId or multiple UIds separated by commas
                     Example: "2c68e1df-1335-4e8c-8ef9-eff1d2005629" (CORRECT - single UId from UId field)
                     Example: "2c68e1df-1335-4e8c-8ef9-eff1d2005629,a3b7f2e8-2446-5f9d-9fa0-f0e2d3116730" (CORRECT - multiple UIds)
                     NOT: 1006715 (WRONG - this is the Id field)
                     NOT: "ROBWOL" (WRONG - this is the IdentityID field)
                     PROMPT: "Please provide one or more identity UIds (32-character GUIDs from the UId field, comma-separated)"

    Optional parameters:
        resource_type_name: Filter by resource type name (e.g., "Active Directory - Security Group")
        resource_type_operator: Operator for resource_type_name filter (default: "CONTAINS")
                               Valid values: "CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"
        compliance_status: Filter by compliance status (e.g., "NOT APPROVED", "APPROVED")
        compliance_status_operator: Operator for compliance_status filter (default: "CONTAINS")
                                   Valid values: "CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"
        account_name: Filter by account name (e.g., "HANULR")
        account_name_operator: Operator for account_name filter (default: "CONTAINS")
                              Valid values: "CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"
        system_name: Filter by system name (e.g., "AD")
        system_name_operator: Operator for system_name filter (default: "CONTAINS")
                             Valid values: "CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"
        identity_name: Filter by identity name (e.g., "ROBERT WOLF")
        identity_name_operator: Operator for identity_name filter (default: "CONTAINS")
                               Valid values: "CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"
        sort_by: Field to sort results by (default: "RESOURCE_NAME")
                Valid values: "RESOURCE_NAME", "IDENTITY_NAME", "ACCOUNT_NAME", "RESOURCE_TYPE",
                             "COMPLIANCE_STATUS", "SYSTEM_NAME", "VALID_FROM", "VALID_TO",
                             "DISABLED", "VIOLATION_STATUS"
        page: Page number to retrieve (default: 1, minimum: 1)
             Use with rows parameter to paginate through large result sets
        rows: Number of rows per page (default: 50, minimum: 1, maximum: 1000)
             Controls page size for pagination

    Returns:
        JSON response with detailed assignments including:
        - total: Total number of assignments matching filters
        - pages: Total number of pages available
        - current_page: The page number returned
        - rows_per_page: Number of rows per page
        - assignments_returned: Number of assignments in current page
        - data: Array of assignment objects for current page
    """
    await auth.ensure_authenticated()
    impersonate_user = auth.get_user_identity()

    logger.debug(
        f"ENTRY - get_calculated_assignments_detailed(identity_ids={identity_ids}, "
        f"resource_type_name={resource_type_name}, compliance_status={compliance_status}, "
        f"account_name={account_name}, system_name={system_name}, page={page}, rows={rows})"
    )

    try:
        # Validate mandatory fields using helper
        error = validate_required_fields(identity_ids=identity_ids)
        if error:
            return error

        # Validate pagination parameters
        if page < 1:
            return build_error_response(
                error_type="InvalidPaginationParameter",
                message=f"Invalid page number: {page}. Page must be >= 1.",
                impersonated_user=impersonate_user,
            )

        if rows < 1 or rows > 1000:
            return build_error_response(
                error_type="InvalidPaginationParameter",
                message=f"Invalid rows per page: {rows}. Rows must be between 1 and 1000.",
                impersonated_user=impersonate_user,
            )

        # Build the filters object dynamically based on provided parameters
        filters = []

        # Add multipleIdentityIds filter only when a GUID was provided
        if identity_ids and identity_ids.strip():
            filters.append(f'multipleIdentityIds: "{identity_ids.strip()}"')

        # Add optional filters only if provided
        if resource_type_name and resource_type_name.strip():
            # Validate operator
            valid_operators = ["CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"]
            if resource_type_operator not in valid_operators:
                return build_error_response(
                    error_type="InvalidOperator",
                    message=f"Invalid resource_type_operator: {resource_type_operator}. Valid values are: {', '.join(valid_operators)}",
                    impersonated_user=impersonate_user,
                )
            filters.append(
                f'resourceTypeName: {{filterValue: "{resource_type_name}", operator: {resource_type_operator}}}'
            )

        if compliance_status and compliance_status.strip():
            # Validate operator
            valid_operators = ["CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"]
            if compliance_status_operator not in valid_operators:
                return build_error_response(
                    error_type="InvalidOperator",
                    message=f"Invalid compliance_status_operator: {compliance_status_operator}. Valid values are: {', '.join(valid_operators)}",
                    impersonated_user=impersonate_user,
                )
            filters.append(
                f'complianceStatus: {{filterValue: "{compliance_status}", operator: {compliance_status_operator}}}'
            )

        if account_name and account_name.strip():
            # Validate operator
            valid_operators = ["CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"]
            if account_name_operator not in valid_operators:
                return build_error_response(
                    error_type="InvalidOperator",
                    message=f"Invalid account_name_operator: {account_name_operator}. Valid values are: {', '.join(valid_operators)}",
                    impersonated_user=impersonate_user,
                )
            filters.append(
                f'accountName: {{filterValue: "{account_name}", operator: {account_name_operator}}}'
            )

        if system_name and system_name.strip():
            # Validate operator
            valid_operators = ["CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"]
            if system_name_operator not in valid_operators:
                return build_error_response(
                    error_type="InvalidOperator",
                    message=f"Invalid system_name_operator: {system_name_operator}. Valid values are: {', '.join(valid_operators)}",
                    impersonated_user=impersonate_user,
                )
            filters.append(
                f'systemName: {{filterValue: "{system_name}", operator: {system_name_operator}}}'
            )

        if identity_name and identity_name.strip():
            # Validate operator
            valid_operators = ["CONTAINS", "EQUAL", "IS_EMPTY", "IS_NOT_EMPTY"]
            if identity_name_operator not in valid_operators:
                return build_error_response(
                    error_type="InvalidOperator",
                    message=f"Invalid identity_name_operator: {identity_name_operator}. Valid values are: {', '.join(valid_operators)}",
                    impersonated_user=impersonate_user,
                )
            filters.append(
                f'identityName: {{filterValue: "{identity_name}", operator: {identity_name_operator}}}'
            )

        # Join filters
        filters_string = ", ".join(filters)

        # Validate sort_by parameter
        valid_sort_options = [
            "RESOURCE_NAME",
            "IDENTITY_NAME",
            "ACCOUNT_NAME",
            "RESOURCE_TYPE",
            "COMPLIANCE_STATUS",
            "SYSTEM_NAME",
            "VALID_FROM",
            "VALID_TO",
            "DISABLED",
            "VIOLATION_STATUS",
        ]
        if sort_by not in valid_sort_options:
            return build_error_response(
                error_type="InvalidSortOption",
                message=f"Invalid sort_by: {sort_by}. Valid values are: {', '.join(valid_sort_options)}",
                impersonated_user=impersonate_user,
            )

        # Build GraphQL query with the filters and pagination
        query = f"""query GetCalculatedAssignmentsDetailed {{
  calculatedAssignments(
    sorting: {{sortOrder: ASCENDING, sortBy: {sort_by}}}
    pagination: {{page: {page}, rows: {rows}}}
    filters: {{{filters_string}}}
  ) {{
    pages
    total
    data {{
      complianceStatus
      violations {{
        description
        violationStatus
      }}
      reason {{
        reasonType
        description
        causeObjectKey
      }}
      validFrom
      validTo
      resource {{
        name
        id
        description
        resourceFolder {{
          id
        }}
      }}
      identity {{
        firstName
        lastName
        displayName
        id
        identityId
      }}
      disabled
      account {{
          accountName
          id
          system {{
            name
            id
          }}
          accountType {{
            name
            id
          }}
        }}
    }}
  }}
}}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with version 2.19 WITH CACHING
        result = await _execute_graphql_request_cached(
            query,
            graphql_version="2.19",
            use_cache=use_cache,
        )

        if result["success"]:
            data = result["data"]
            # Extract calculated assignments from the GraphQL response
            if "data" in data and "calculatedAssignments" in data["data"]:
                calculated_assignments = data["data"]["calculatedAssignments"]
                assignments_data = calculated_assignments.get("data", [])
                total = calculated_assignments.get("total", 0)
                pages = calculated_assignments.get("pages", 0)

                return build_success_response(
                    data=assignments_data,
                    endpoint=result["endpoint"],
                    identity_ids=identity_ids,
                    impersonated_user=impersonate_user,
                    resource_type_name=resource_type_name,
                    compliance_status=compliance_status,
                    total_assignments=total,
                    pages=pages,
                    current_page=page,
                    rows_per_page=rows,
                    assignments_returned=len(assignments_data),
                    assignments=assignments_data,
                )
            else:
                return build_error_response(
                    error_type="NoAssignmentsFound",
                    message="No calculated assignments found in response",
                    identity_ids=identity_ids,
                    impersonated_user=impersonate_user,
                    response=data,
                )
        else:
            # Handle GraphQL request failure using helper
            return build_error_response(
                error_type=result.get("error_type", "GraphQLError"),
                result=result,
                identity_ids=identity_ids,
                impersonated_user=impersonate_user,
            )

    except Exception as e:
        return build_error_response(
            error_type=type(e).__name__,
            message=str(e),
            identity_ids=identity_ids,
            impersonated_user=impersonate_user,
        )


@with_rate_limit
@with_function_logging
@mcp.tool()
async def get_compliance_workbench_survey_and_compliance_status() -> str:
    """
    Get compliance workbench configuration including compliance status values and survey templates from Omada GraphQL API.

    This function retrieves the compliance workbench configuration which includes:
    - Compliance status values (name and value pairs)
    - Survey templates (with ID, name, type, system name, and survey initiation activity ID)

    Returns:
        JSON response with compliance workbench configuration including:
        - complianceStatus: Array of {name, value} objects
        - surveyTemplates: Array of survey template objects with id, name, type, systemName, and surveyInitiationActivityId
    """
    try:
        await auth.ensure_authenticated()
        impersonate_user = auth.get_user_identity()

        # Build GraphQL query (no parameters needed for this query)
        query = """query GetComplianceWorkbenchConfiguration {
  complianceWorkbenchConfiguration {
    complianceStatus {
      name
      value
    }
    surveyTemplates {
      name
      id
      surveyTemplateType
      systemName
      surveyInitiationActivityId
    }
  }
}"""

        logger.debug(f"GraphQL query: {query}")

        # Execute GraphQL request with version 3.0
        result = await _execute_graphql_request(
            query=query,
            graphql_version="3.0",
        )

        if result["success"]:
            data = result["data"]
            # Extract compliance workbench configuration from the GraphQL response
            if "data" in data and "complianceWorkbenchConfiguration" in data["data"]:
                config = data["data"]["complianceWorkbenchConfiguration"]

                compliance_status = config.get("complianceStatus", [])
                survey_templates = config.get("surveyTemplates", [])

                return build_success_response(
                    data={
                        "compliance_status": compliance_status,
                        "survey_templates": survey_templates,
                    },
                    endpoint=result["endpoint"],
                    impersonated_user=impersonate_user,
                    compliance_status_count=len(compliance_status),
                    survey_templates_count=len(survey_templates),
                )
            else:
                return build_error_response(
                    error_type="NoConfigurationFound",
                    message="No compliance workbench configuration found in response",
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
