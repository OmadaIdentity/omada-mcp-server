# api/odata.py
"""OData client helper functions."""
import logging
import os

logger = logging.getLogger("server")


def _get_omada_base_url(omada_base_url: str = None) -> str:
    """
    Get Omada base URL from parameter or environment variable.

    Args:
        omada_base_url: Optional base URL parameter

    Returns:
        Base URL with trailing slash removed

    Raises:
        Exception if base URL not found in parameter or environment
    """
    if not omada_base_url:
        omada_base_url = os.getenv("OMADA_BASE_URL")
        if not omada_base_url:
            raise Exception(
                "OMADA_BASE_URL not found in environment variables or parameters"
            )
    return omada_base_url.rstrip("/")


def _build_odata_filter(field_name: str, value: str, operator: str) -> str:
    """
    Build an OData filter expression based on the operator.

    Args:
        field_name: The field name (e.g., "FIRSTNAME", "LASTNAME")
        value: The value to filter by
        operator: The OData operator (eq, ne, contains, startswith, etc.)

    Returns:
        OData filter expression string
    """
    # Escape single quotes in value
    escaped_value = value.replace("'", "''")

    if operator in ["eq", "ne", "gt", "ge", "lt", "le", "like"]:
        # Standard comparison operators (including LIKE)
        return f"{field_name} {operator} '{escaped_value}'"
    elif operator == "contains":
        # Contains function
        return f"contains({field_name}, '{escaped_value}')"
    elif operator == "startswith":
        # Starts with function
        return f"startswith({field_name}, '{escaped_value}')"
    elif operator == "endswith":
        # Ends with function
        return f"endswith({field_name}, '{escaped_value}')"
    elif operator == "substringof":
        # Substring of function (reverse of contains)
        return f"substringof('{escaped_value}', {field_name})"
    else:
        # Fallback to eq if unknown operator
        return f"{field_name} eq '{escaped_value}'"


def _summarize_entities(data: dict, entity_type: str) -> dict:
    """
    Create a summarized version of entity data with only key fields.

    Args:
        data: The full OData response
        entity_type: Type of entity being summarized

    Returns:
        Summarized data with key fields only
    """
    if not data or "value" not in data:
        return data

    # Define key fields for each entity type
    summary_fields = {
        "Identity": [
            "Id",
            "UId",
            "DISPLAYNAME",
            "FIRSTNAME",
            "LASTNAME",
            "EMAIL",
            "EMPLOYEEID",
            "DEPARTMENT",
            "STATUS",
        ],
        "Resource": [
            "Id",
            "DISPLAYNAME",
            "DESCRIPTION",
            "RESOURCEKEY",
            "STATUS",
            "Systemref",
        ],
        "Role": ["Id", "DISPLAYNAME", "DESCRIPTION", "STATUS"],
        "Account": ["Id", "ACCOUNTNAME", "DISPLAYNAME", "STATUS", "SYSTEM"],
        "Application": ["Id", "DISPLAYNAME", "DESCRIPTION", "STATUS"],
        "System": ["Id", "DISPLAYNAME", "DESCRIPTION", "STATUS"],
        "CalculatedAssignments": [
            "Id",
            "AssignmentKey",
            "AccountName",
            "Identity",
            "Resource",
        ],
        "AssignmentPolicy": ["Id", "DISPLAYNAME", "DESCRIPTION", "STATUS"],
    }

    # Get relevant fields for this entity type
    fields_to_keep = summary_fields.get(
        entity_type, ["Id", "DISPLAYNAME", "DESCRIPTION"]
    )

    summarized_entities = []
    for entity in data.get("value", []):
        summary = {}
        for field in fields_to_keep:
            if field in entity:
                value = entity[field]
                # Truncate long text fields
                if isinstance(value, str) and len(value) > 100:
                    summary[field] = value[:97] + "..."
                else:
                    summary[field] = value

        # Always include Id if available
        if "Id" in entity and "Id" not in summary:
            summary["Id"] = entity["Id"]

        summarized_entities.append(summary)

    # Return summarized data with same structure
    result = data.copy()
    result["value"] = summarized_entities
    return result
