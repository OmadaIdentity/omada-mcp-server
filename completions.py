# completions.py - MCP Completions for Omada Identity Server
#
# Completions provide autocomplete suggestions for function arguments,
# helping users discover valid values for parameters.

import logging

logger = logging.getLogger("server")


def register_completions(mcp):
    """Register all MCP completions with the FastMCP server."""

    @mcp.completion()
    async def complete_arguments(argument_name: str, argument_value: str) -> list[str]:
        """
        Provide completions for common Omada function arguments.

        This handles completions for:
        - system_id: System identifiers
        - resource_type_name: Resource type names
        - field: Identity field names (for OData queries)
        - filter_field: Field names for filtering
        """

        # System ID completions
        if argument_name in ["system_id", "systemId"]:
            # System IDs are environment-specific UUIDs — no static suggestions.
            # Use query_omada_resources or query_omada_entities to discover real system IDs
            # from your own Omada environment.
            return []

        # Resource Type Name completions
        if argument_name in ["resource_type_name", "resourceTypeName", "resource_type"]:
            return [
                "Active Directory - Security Group",
                "Active Directory - Distribution List",
                "Active Directory - User Account",
                "Azure AD - Security Group",
                "Azure AD - Application Role",
                "SAP - Role",
                "SAP - Profile",
                "Salesforce - Permission Set",
                "Salesforce - Profile",
                "ServiceNow - Role",
                "ServiceNow - Group",
                "Google Workspace - Group",
                "Okta - Group",
                "Database - User",
                "Database - Role",
                "SharePoint - Site Permission",
                "Exchange - Mailbox Permission",
                "Network Share - Folder Permission",
                "VPN Access",
                "Application Access",
            ]

        # Identity Field Name completions (for OData queries)
        if argument_name in ["field", "field_name", "filter_field"]:
            return [
                "EMAIL",
                "FIRSTNAME",
                "LASTNAME",
                "DISPLAYNAME",
                "EMPLOYEEID",
                "DEPARTMENT",
                "TITLE",
                "MANAGER",
                "LOCATION",
                "COMPANY",
                "COSTCENTER",
                "STATUS",
                "STARTDATE",
                "ENDDATE",
                "USERID",
                "UId",
                "Id",
                "PHONENUMBER",
                "MOBILENUMBER",
                "OFFICE",
                "DIVISION",
                "BUSINESSUNIT",
            ]

        # OData Operator completions
        if argument_name in ["operator", "filter_operator"]:
            return [
                "eq",  # equals
                "ne",  # not equals
                "gt",  # greater than
                "ge",  # greater than or equal
                "lt",  # less than
                "le",  # less than or equal
                "contains",  # contains substring
                "startswith",  # starts with
                "endswith",  # ends with
            ]

        # Compliance Status completions
        if argument_name in ["compliance_status", "complianceStatus"]:
            return [
                "APPROVED",
                "NOT APPROVED",
                "VIOLATION",
                "PENDING",
                "REVIEW REQUIRED",
            ]

        # Workflow Step completions
        if argument_name in ["workflow_step", "workflowStep"]:
            return [
                "ManagerApproval",
                "ResourceOwnerApproval",
                "SystemOwnerApproval",
                "ComplianceApproval",
                "SecurityApproval",
            ]

        # Status completions (for access requests)
        if argument_name == "status":
            return [
                "PENDING",
                "APPROVED",
                "REJECTED",
                "CANCELLED",
                "IN_PROGRESS",
                "COMPLETED",
            ]

        # Return empty list if no completions available
        return []

    logger.info(
        "Registered MCP completions for: system_id, resource_type_name, field names, operators, compliance_status, workflow_step, status"
    )
