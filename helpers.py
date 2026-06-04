"""
Helper functions for Omada MCP Server to reduce code duplication.

This module contains utility functions for:
- Field validation
- Error response building
- Success response building
"""

import json
from typing import Any, Optional


def validate_required_fields(**kwargs) -> Optional[str]:
    """
    Validate that required fields are non-empty.

    Returns error JSON string if validation fails, None if all valid.

    Args:
        **kwargs: Field name and value pairs to validate

    Returns:
        JSON error string if validation fails, None if all fields are valid

    Example:
        error = validate_required_fields(
            impersonate_user=impersonate_user,
            identity_id=identity_id
        )
        if error:
            return error
    """
    for field_name, field_value in kwargs.items():
        if field_value is None or (
            isinstance(field_value, str) and not field_value.strip()
        ):
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Missing required field: {field_name}",
                    "error_type": "ValidationError",
                },
                indent=2,
            )
    return None


def build_error_response(
    error_type: str, result: dict = None, message: str = None, **extra_fields
) -> str:
    """
    Build standardized error response in JSON format.

    Args:
        error_type: Type of error (GraphQLError, ValidationError, Exception, etc.)
        result: Optional result dict from _execute_graphql_request to extract error details
        message: Optional custom error message
        **extra_fields: Additional context fields to include (impersonated_user, identity_id, etc.)

    Returns:
        JSON string with standardized error format

    Example:
        return build_error_response(
            error_type="GraphQLError",
            result=result,
            impersonated_user=impersonate_user,
            identity_id=identity_id
        )

        # Or with custom message
        return build_error_response(
            error_type="ValidationError",
            message="Invalid decision value",
            impersonated_user=impersonate_user
        )
    """
    error_result = {"status": "error", "error_type": error_type, **extra_fields}

    # Add custom message if provided
    if message:
        error_result["message"] = message

    # Extract error details from result if provided
    if result:
        if "status_code" in result:
            error_result["status_code"] = result["status_code"]
        if "error" in result:
            error_result["error"] = result["error"]
        if "endpoint" in result:
            error_result["endpoint"] = result["endpoint"]
        # Handle GraphQL errors array
        if "errors" in result:
            error_result["errors"] = result["errors"]

    return json.dumps(error_result, indent=2)


def build_success_response(data: Any = None, endpoint: str = None, **context) -> str:
    """
    Build standardized success response in JSON format.

    Args:
        data: The main response data (can be dict, list, or any JSON-serializable type)
        endpoint: Optional API endpoint that was called
        **context: Context fields to include (impersonated_user, identity_id, etc.)

    Returns:
        JSON string with standardized success format

    Example:
        return build_success_response(
            data=assignments,
            endpoint=result["endpoint"],
            impersonated_user=impersonate_user,
            identity_id=identity_id,
            total_count=len(assignments)
        )
    """
    response = {"status": "success", **context}

    # Add data if provided (could be None for some operations)
    if data is not None:
        response["data"] = data

    # Add endpoint if provided
    if endpoint:
        response["endpoint"] = endpoint

    return json.dumps(response, indent=2)


def build_pagination_clause(page: int = None, rows: int = None) -> str:
    """
    Build GraphQL pagination clause for queries.

    Args:
        page: Page number for pagination (e.g., 1, 2, 3...)
        rows: Number of rows per page (e.g., 10, 20, 50...)

    Returns:
        Formatted pagination clause string for GraphQL query, or empty string if parameters not provided

    Example:
        # With pagination
        pagination = build_pagination_clause(page=2, rows=20)
        # Returns: "pagination: {page: 2, rows: 20}, "

        # Without pagination
        pagination = build_pagination_clause()
        # Returns: ""

        # Usage in query
        query = f'''query {{
          identities(
            {build_pagination_clause(page=1, rows=10)}filters: {{}}
          ) {{
            data {{ id }}
          }}
        }}'''
    """
    if page is not None and rows is not None:
        return f"pagination: {{page: {page}, rows: {rows}}}, "
    return ""


def json_to_graphql_syntax(resources_json: str) -> str:
    """
    Convert JSON string format to GraphQL syntax for resources.

    Args:
        resources_json: JSON string representation of resources
                       Example: '[{"id": "123"}]' or '{"id": "123"}'

    Returns:
        GraphQL syntax string
        Example: '[{id: "123"}]' or '{id: "123"}'

    Raises:
        ValueError: If the input is not valid JSON

    Example:
        # Single resource
        json_input = '{"id": "resource-123"}'
        graphql_output = json_to_graphql_syntax(json_input)
        # Returns: '{id: "resource-123"}'

        # Multiple resources
        json_input = '[{"id": "res1"}, {"id": "res2"}]'
        graphql_output = json_to_graphql_syntax(json_input)
        # Returns: '[{id: "res1"}, {id: "res2"}]'
    """
    try:
        # Parse the JSON string
        parsed = json.loads(resources_json)

        def convert_dict_to_graphql(obj):
            """Convert a dictionary to GraphQL syntax."""
            if isinstance(obj, dict):
                pairs = []
                for key, value in obj.items():
                    if isinstance(value, str):
                        pairs.append(f'{key}: "{value}"')
                    elif isinstance(value, (int, float, bool)):
                        pairs.append(f"{key}: {str(value).lower()}")
                    elif isinstance(value, dict):
                        pairs.append(f"{key}: {convert_dict_to_graphql(value)}")
                    elif isinstance(value, list):
                        list_items = [
                            (
                                convert_dict_to_graphql(item)
                                if isinstance(item, dict)
                                else f'"{item}"' if isinstance(item, str) else str(item)
                            )
                            for item in value
                        ]
                        pairs.append(f'{key}: [{", ".join(list_items)}]')
                    else:
                        pairs.append(f'{key}: "{str(value)}"')
                return "{" + ", ".join(pairs) + "}"
            elif isinstance(obj, str):
                return f'"{obj}"'
            elif isinstance(obj, (int, float, bool)):
                return str(obj).lower()
            else:
                return f'"{str(obj)}"'

        if isinstance(parsed, list):
            # Handle array of resources
            graphql_items = [convert_dict_to_graphql(item) for item in parsed]
            return "[" + ", ".join(graphql_items) + "]"
        elif isinstance(parsed, dict):
            # Handle single resource
            return convert_dict_to_graphql(parsed)
        else:
            raise ValueError(f"Unsupported type for GraphQL conversion: {type(parsed)}")

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {str(e)}")
