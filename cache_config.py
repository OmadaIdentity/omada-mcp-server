"""
cache_config.py — Per-operation cache TTL configuration.

Different Omada data types change at very different rates:
- Compliance config / resource lists → hours (very static)
- Calculated assignments              → minutes (changes on provisioning events)
- Pending approvals                   → seconds/minutes (actioned in real time)

The OPERATION_TTLS dict maps GraphQL operation root field names (lowercase)
to TTL in seconds. get_ttl_for_operation() scans the query string for a
matching key and returns the corresponding TTL.

Override the default TTL at the environment level via CACHE_TTL_SECONDS.
"""

import os

# Base TTL from environment (fallback when no specific rule matches)
_DEFAULT_TTL: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

# Per-operation TTLs — key is a substring of the GraphQL query (lowercase)
OPERATION_TTLS: dict[str, int] = {
    # Very static — only changes on Omada upgrades or admin config changes
    "complianceworkbenchconfiguration": 7200,   # 2 hours

    # Fairly static — resource catalogue changes infrequently
    "requestableresources": 1800,               # 30 minutes
    "accessrequestcomponents": 1800,            # 30 minutes

    # Moderate churn — assignments change on provisioning events
    "calculatedassignments": 900,               # 15 minutes

    # Identity data — changes on HR events
    "identities": 1800,                         # 30 minutes
    "identitycontexts": 1800,                   # 30 minutes

    # High churn — approvals are actioned in real time
    "pendingapprovalsurveyquestions": 120,       # 2 minutes

    # Access requests — submitted and processed quickly
    "accessrequests": 300,                      # 5 minutes
}


def get_ttl_for_operation(query: str, is_mutation: bool = False) -> int:
    """
    Return the TTL (seconds) to use when caching a GraphQL response.

    Returns 0 for mutations — write operations are never cached.
    Scans the query string for known operation root fields and returns
    the matching TTL, or the default TTL if no match is found.

    Args:
        query:       GraphQL query/mutation string
        is_mutation: True if the operation is a mutation (write)

    Returns:
        TTL in seconds, or 0 to skip caching
    """
    if is_mutation:
        return 0

    query_lower = query.lower()
    for operation, ttl in OPERATION_TTLS.items():
        if operation in query_lower:
            return ttl

    return _DEFAULT_TTL
