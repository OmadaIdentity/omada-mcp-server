# prompts.py - MCP Prompts for Omada Identity Server
#
# Prompts help guide Claude through complex workflows by providing
# templates and examples for common Omada operations.

import logging

logger = logging.getLogger("server")


def register_prompts(mcp):
    """Register all MCP prompts with the FastMCP server."""

    @mcp.prompt()
    def request_access_workflow():
        """
        Guide user through requesting access to a resource in Omada.

        This prompt helps with the complete access request workflow:
        1. Find the user's identity
        2. Get available contexts
        3. List requestable resources
        4. Create the access request
        """
        return """I'll help you request access to resources in Omada. Authentication is automatic — no bearer token needed.

**Step 1: Identify the User**
The authenticated user's email is used automatically. I can also look up another person's identity by email.

Required information:
- Email address of the person requesting access (defaults to the authenticated user)

**Step 2: Get User's Business Contexts**
I'll call get_identity_contexts to retrieve the business contexts available to this user.

IMPORTANT: "Context" means organizational context (department, cost center, team, etc.)
- NOT the reason or justification for access
- These come from get_identity_contexts tool
- You must select one context ID from the list returned
- Example contexts: "Sales Department", "Engineering Team", "Project Alpha"

**Step 3: List Available Resources**
Then I'll show you what resources this user can request access to.

Optional filters:
- System ID (to filter by specific system)
- Context ID (to filter resources by the business context selected)

**Step 4: Create Access Request**
Finally, I'll create the access request with TWO separate pieces of information:

1. **context** (REQUIRED): The business context ID from Step 2
   - This is an ID like "6dd03400-ddb5-4cc4-bfff-490d94b195a9"
   - Selected from the contexts returned by get_identity_contexts
   - Example: If contexts shows "Sales Department" with id "abc-123", use "abc-123"

2. **reason** (REQUIRED): Your justification text
   - This is a free-text explanation
   - Example: "Need to access project website that requires VPN connection"
   - Example: "Requires access to customer database for Q1 reporting"

Plus:
- Resource(s) to request (from Step 3)
- Optional: Valid from/to dates

Let's get started! Please provide:
1. The email address of the person requesting access (or confirm it's for yourself)
"""

    @mcp.prompt()
    def approve_requests_workflow():
        """
        Guide user through approving pending access requests in Omada.

        This prompt helps with the approval workflow:
        1. Get pending approvals
        2. Review approval details
        3. Make approval decision
        """
        return """I'll help you review and approve pending access requests in Omada. Authentication is automatic.

**Step 1: Get Pending Approvals**
I'll retrieve all access requests waiting for your approval as the authenticated user.

Optional:
- Workflow step filter (ManagerApproval, ResourceOwnerApproval, SystemOwnerApproval)

**Step 2: Review Details**
I'll show you the approval details including:
- Who requested access
- What resource they're requesting
- The reason for the request
- Workflow step information

**Step 3: Make Decision with Human Confirmation**

CRITICAL SECURITY PROTOCOL - I MUST follow these steps:

Before calling make_approval_decision, I will:

1. Display complete request details in a clear summary:
   ```
   ═══════════════════════════════════════════════
   APPROVAL DECISION SUMMARY
   ═══════════════════════════════════════════════
   Requester: [Full Name] ([Email])
   Resource: [Resource Name]
   System: [System Name]
   Justification: [Reason provided]
   Workflow Step: [Current approval step]
   Survey ID: [ID]
   Survey Object Key: [Key]
   ═══════════════════════════════════════════════
   ```

2. Ask you explicitly:
   "**Do you want to APPROVE, REJECT, or CANCEL this request?**

   Please type one of:
   - APPROVE (to grant access)
   - REJECT (to deny access)
   - CANCEL (to skip this request)"

3. Wait for your explicit response

4. Only call make_approval_decision AFTER you have typed APPROVE or REJECT

I will NEVER call make_approval_decision without:
- Showing you the complete request details
- Getting your explicit APPROVE/REJECT/CANCEL response
- Confirming your decision before execution

This ensures you have full control and visibility before any approval decision is made.

Let's begin! Would you like to:
- See all pending approvals?
- Filter by workflow step (ManagerApproval, ResourceOwnerApproval, SystemOwnerApproval)?
"""

    @mcp.prompt()
    def search_identity_workflow():
        """
        Guide user through searching for identities in Omada.

        This prompt provides examples of different search methods.
        """
        return """I'll help you search for identities in the Omada system. Authentication is automatic.

**Search Methods:**

**1. Search by Email** (most common)
- Field: EMAIL
- Example: EMAIL eq 'user@domain.com'

**2. Search by Name**
- Field: FIRSTNAME and/or LASTNAME
- Example: FIRSTNAME eq 'John' and LASTNAME eq 'Smith'
- Supports operators: eq, contains, startswith

**3. Search by Department**
- Field: DEPARTMENT
- Example: DEPARTMENT contains 'Engineering'

**4. Search by Employee ID**
- Field: EMPLOYEEID
- Example: EMPLOYEEID eq 'EMP12345'

**Important Field Names (use EXACTLY as shown):**
- EMAIL (not "email", "MAIL", or "EMAILADDRESS")
- FIRSTNAME (not "firstname" or "first_name")
- LASTNAME (not "lastname" or "last_name")
- DISPLAYNAME, EMPLOYEEID, DEPARTMENT, STATUS

**What would you like to search for?**
Provide:
1. The search criteria (field and value)
"""

    @mcp.prompt()
    def review_assignments_workflow():
        """
        Guide user through reviewing a user's resource assignments and compliance.

        This prompt helps with:
        1. Finding the user's identity
        2. Getting their calculated assignments
        3. Reviewing compliance and violations
        """
        return """I'll help you review a user's resource assignments and compliance status in Omada.

**What This Shows:**
- All resources assigned to the user
- Compliance status (APPROVED, NOT APPROVED, etc.)
- Violation details if any
- Account information
- Resource details including system and type

**Optional Filters:**
- Resource Type Name (e.g., "Active Directory - Security Group")
- Compliance Status (e.g., "NOT APPROVED", "APPROVED")

**Step 1: Find the User**
First, I need to identify the user. I can search by:
- Email address
- Employee ID
- Name

**Step 2: Get Assignments**
I'll retrieve their calculated assignments with full details.

**Step 3: Review Results**
I'll show you:
- Compliant assignments
- Non-compliant assignments with violation reasons
- Resource and account details

To begin, please provide:
1. How to identify the user (email, employee ID, or name)
2. Optional: Resource type or compliance status filter
"""

    @mcp.prompt()
    def authentication_workflow():
        """
        Explain the automatic authentication model used by this server.
        """
        return """Authentication is fully automatic — no bearer tokens or manual setup required.

**How Authentication Works:**

1. **First Use — Browser Login**
   The first time any tool is called, a browser window opens automatically.
   - Sign in with your Microsoft credentials
   - Approve the permissions for Omada access
   - The browser tab closes and the tool proceeds automatically

2. **Subsequent Calls — Silent Token Refresh**
   After the first login, a refresh token is cached and used silently.
   - No browser window opens
   - Tokens refresh in the background before they expire
   - You stay logged in across server restarts

3. **Token Security**
   - Tokens are **encrypted at rest** using Windows DPAPI
   - Only your Windows user account can decrypt the token file
   - The Bearer token is **never exposed** in tool responses or logs
   - Token cache is excluded from git via `.gitignore`

4. **Logging Out**
   To force re-authentication (e.g., to switch accounts):
   - Call the `logout` tool
   - Or delete `.token_cache.bin` manually
   - The next tool call will open the browser again

5. **Checking Configuration**
   Call `check_omada_config` to verify your connection settings.
   Call `ping` to confirm the server is reachable.

**No action required** — authentication happens automatically when you use any tool.
"""

    @mcp.prompt()
    def troubleshooting_workflow():
        """
        Common troubleshooting tips for Omada MCP operations.
        """
        return """Here are solutions to common issues:

**Issue: Browser window opens on every call**
Solution: The token cache file may be missing or corrupt.
- Call `logout` to clear state, then retry — this resets the cache cleanly
- Check that `.token_cache.bin` exists after authenticating
- Ensure Windows DPAPI is available (domain or local Windows account)

**Issue: "Could not find identity for email"**
Solution: Check that:
- Email address is correct and complete
- Field name is "EMAIL" (all caps)
- User exists in Omada system
- Your authenticated account has access to identity data

**Issue: "No resources found"**
Solution: This could mean:
- User doesn't have permission to request any resources
- Filters are too restrictive (try removing filters)
- Context or system ID is incorrect

**Issue: "Invalid entity type"**
Solution: Valid entity types are:
- Identity, Resource, Role, Account, Application, System, CalculatedAssignments, AssignmentPolicy

**Issue: "Authentication failed" or 401 error**
Solution:
1. Call the `logout` tool to clear the cached token
2. Retry the tool — a browser login window will open
3. If the issue persists, check `check_omada_config` to verify OMADA_BASE_URL and CLIENT_ID

**Issue: "403 Forbidden" or permission error**
Solution: Your account doesn't have permission for this operation.
- Verify your Omada role includes the required permissions
- Contact your Omada administrator
- Try `check_omada_config` to confirm the environment settings

**Issue: Field Names Not Working**
Solution: Omada field names are UPPERCASE and specific:
- Use "EMAIL" not "email", "MAIL", or "EMAILADDRESS"
- Use "FIRSTNAME" not "firstname" or "FirstName"
- Use "LASTNAME" not "lastname" or "LastName"

**Still having issues?**
Check the logs:
- Log file location shown at startup
- Set LOG_LEVEL_<function_name>=DEBUG in .env for detailed logs
- Call `get_cache_stats` to check if stale cache is involved — use `clear_cache` if needed
"""

    @mcp.prompt()
    def bulk_access_request_workflow():
        """
        Guide user through requesting access for multiple users (beneficiaries).

        This prompt helps with bulk access request operations:
        1. Get list of identities (potential beneficiaries)
        2. Select multiple users
        3. Find common resources they can request
        4. Create access requests for multiple users
        """
        return """I'll help you request access for multiple users at once in Omada. Authentication is automatic.

**Bulk Access Request Workflow:**

**Step 1: Find Beneficiaries**
First, I'll get a list of identities that can be beneficiaries in access requests.
I'll use get_identities_for_beneficiary to retrieve the list.

Optional pagination:
- Page number (e.g., 1, 2, 3)
- Rows per page (e.g., 10, 20, 50)

**Step 2: Get Business Contexts for Each User**
For each selected identity, I'll call get_identity_contexts to get their available business contexts.

IMPORTANT: "Context" means organizational context
- Examples: Department, Cost Center, Team, Project
- NOT the reason/justification for access
- Each user may have different contexts available
- Must select appropriate context for each user

**Step 3: Find Requestable Resources**
For selected users, I'll find what resources they can request.
You can filter by:
- System ID
- Context ID (from the contexts retrieved in Step 2)
- Resource name

**Step 4: Create Multiple Requests**
I'll create access requests for each selected user with TWO separate fields:

1. **context** (REQUIRED): Business context ID from get_identity_contexts
   - Different for each user based on their available contexts
   - Must be a valid context ID for that specific user
   - Example: "6dd03400-ddb5-4cc4-bfff-490d94b195a9"

2. **reason** (REQUIRED): Your justification text
   - Same reason for all users (or customized per user)
   - Free-text explanation
   - Example: "New team member onboarding - requires VPN access"
   - Example: "Quarterly project team access provisioning"

Plus:
- Same resources for all
- Optional: Same validity dates

**Benefits of Bulk Operations:**
- Save time when onboarding multiple users
- Ensure consistent access across teams
- Standardize access request reasons

Let's begin! Please provide:
1. How many users you'd like to process (for pagination planning)
2. Optional: Pagination settings (page and rows)
"""

    @mcp.prompt()
    def compliance_audit_workflow():
        """
        Guide user through auditing compliance violations across users.

        This prompt helps with compliance auditing:
        1. Search for users by department or criteria
        2. Get their calculated assignments
        3. Filter by compliance status
        4. Review violations
        """
        return """I'll help you audit compliance violations in Omada. Authentication is automatic.

**Compliance Audit Workflow:**

**Step 1: Define Audit Scope**
Choose how to identify users to audit:
- By department (e.g., DEPARTMENT contains 'Engineering')
- By status (e.g., STATUS eq 'ACTIVE')
- All users (use with caution on large datasets)

**Step 2: Get User Assignments**
For each user, I'll retrieve their calculated assignments including:
- Compliance status
- Violation details
- Resource assignments
- Account information

**Step 3: Filter Violations**
Focus on specific issues:
- Compliance Status filter: "NOT APPROVED", "VIOLATION", "APPROVED"
- Resource Type filter: e.g., "Active Directory - Security Group"

**Step 4: Review Results**
I'll provide a summary showing:
- Users with violations
- Type of violations
- Affected resources
- Violation descriptions

**Common Compliance Checks:**
- Segregation of Duties (SoD) violations
- Unauthorized access (NOT APPROVED)
- Expired assignments
- Missing approvals

**Important Notes:**
- This uses the GUID (UId) field, not the IdentityID field
- Results show detailed violation information
- Can be filtered by resource type for targeted audits

To begin, please provide:
1. Search criteria for users to audit
2. Optional: Compliance status filter (default: all statuses)
"""

    @mcp.prompt()
    def resource_discovery_workflow():
        """
        Guide user through discovering and exploring resources in Omada systems.

        This prompt helps with:
        1. Listing resources by system
        2. Filtering by resource type
        3. Understanding resource structure
        """
        return """I'll help you discover and explore resources available in Omada systems. Authentication is automatic.

**Resource Discovery Workflow:**

**Step 1: Understand Resource Types**
Resources in Omada are organized by:
- System (e.g., Active Directory, SAP, Salesforce)
- Resource Type (e.g., Security Groups, Roles, Permissions)

**Step 2: List Resources**
I can show you resources filtered by:
- System ID
- Resource Type ID or Name
- Resource name (partial match)

**Two Discovery Modes:**

**A. Administrative Discovery** (query_omada_resources)
- Shows ALL resources in the system
- Administrative view, not user-scoped
- Good for: Inventory, reporting, analysis
- NOT for: Access request workflows

**B. Requestable Resources** (get_resources_for_beneficiary)
- Shows resources a specific user CAN request
- User-scoped and permission-aware
- Good for: Access requests, user onboarding
- Requires: Identity ID (UId field - 32 character GUID)

**Step 3: Explore Resource Details**
For each resource, I'll show:
- Name and description
- System it belongs to
- Resource type
- Resource ID (for access requests)

**Step 4: Use Resources**
Once you find resources, you can:
- Request access to them
- Use IDs for access request creation
- Filter by system or context

**Important: Which Function to Use?**
- Planning an access request? → Use get_resources_for_beneficiary
- Administrative inventory? → Use query_omada_resources
- Want to see what a user can request? → Use get_resources_for_beneficiary

Let's start exploring! Please provide:
1. What are you looking for? (discovery mode: admin inventory or requestable resources)
2. Optional: System ID, resource type, or resource name filter
"""

    @mcp.prompt()
    def identity_context_workflow():
        """
        Guide user through understanding and using identity contexts.

        This prompt explains contexts and how they're used in access requests.
        """
        return """I'll help you understand and work with identity contexts in Omada.

**What are Identity Contexts?**
Contexts represent business dimensions like:
- Organizational units (departments, teams)
- Cost centers
- Projects
- Locations
- Other business hierarchies

**Why Contexts Matter:**
- Required for creating access requests
- Determine what resources are available
- Help route approvals to the right people
- Filter resources by business scope

**Working with Contexts:**

**Step 1: Get Contexts for an Identity**
I'll show all contexts available for a specific user.

Required:
- Identity ID (UId field - 32 character GUID, NOT the IdentityID field)

**Step 2: Understanding Context Data**
Each context includes:
- Context ID (use this for access requests)
- Display name
- Context type

**Step 3: Using Contexts**
Contexts are used when:
- Creating access requests (required field)
- Filtering requestable resources
- Routing approvals through workflows

**Common Context Operations:**

1. **Find User's Contexts:**
   - Get identity UId from user lookup
   - Call get_identity_contexts
   - Review available contexts

2. **Filter Resources by Context:**
   - Use context_id parameter
   - Shows only resources available in that context

3. **Create Access Request:**
   - Select appropriate context
   - Provide context ID in request

**Important: Identity ID vs IdentityID**
- Use the "UId" field (32-character GUID like "e3e869c4-...")
- NOT the "IdentityID" field (like "ROBWOL")
- NOT the "Id" field (integer like 1006715)

Let's explore contexts! Please provide:
1. How to identify the user (email, employee ID, or name)
"""

    @mcp.prompt()
    def compare_identities_workflow():
        """
        Guide user through comparing two identities side-by-side.

        This prompt helps with detailed identity comparison including:
        1. Finding both identities by name
        2. Comparing organizational metadata (department, manager, etc.)
        3. Comparing access requests
        4. Comparing calculated assignments (current access)
        5. Comparing assignment policies used
        """
        return """I'll help you perform a detailed comparison of two identities in Omada. Authentication is automatic.

**Identity Comparison Workflow:**

This workflow will compare two users across multiple dimensions:
- Organizational metadata (department, manager, location, etc.)
- Active access requests
- Current resource assignments
- Compliance status
- Assignment policies affecting each user

**Step 1: Identify First User**
I need to find the first identity. Please provide identifying information:
- First name and last name, OR
- Email address, OR
- Employee ID

**Step 2: Identify Second User**
Now I need the second identity to compare against. Please provide:
- First name and last name, OR
- Email address, OR
- Employee ID

**Step 3: Retrieve Detailed Identity Data**
For each user, I'll retrieve:

**A. OData Identity Information:**
- UId (unique identifier)
- IdentityID (user ID)
- Email, First Name, Last Name
- Department, Job Title, Manager
- Employee ID, Status
- Location, Cost Center
- Other organizational attributes

**B. Access Requests (using get_access_requests):**
I'll query access requests for each user by filtering on their identity:
- Active access requests (status: PENDING, IN_PROGRESS)
- Completed requests (status: APPROVED, REJECTED)
- Requested resources and systems
- Request timestamps and justifications
- Current approval status

**C. Pending Approvals (using get_pending_approvals):**
If users are approvers, I'll retrieve:
- Requests waiting for their approval
- Workflow step they're approving at
- Requester information
- Resource details
- Survey IDs for approval tracking

**D. Calculated Assignments (Current Access):**
- All assigned resources
- Compliance status for each assignment
- Violation details if any
- Account information
- Resource types and systems

**E. Assignment Policy Context:**
- Policies that grant access automatically
- Assignment reasons
- Resource assignment sources

**Step 4: Generate Comparison Report**
I'll provide a detailed side-by-side comparison showing:

**Organizational Comparison:**
- Departments (same/different)
- Job titles
- Managers (same/different)
- Locations
- Cost centers
- Employment status

**Access Request Comparison:**
- Active access requests for User 1 vs User 2
- Number of pending requests
- Recently approved/rejected requests
- Common resources being requested
- Request patterns and frequencies
- Justifications and reasons provided
- Request timestamps and age

**Pending Approvals Comparison:**
- Approval queue for User 1 vs User 2 (if they are approvers)
- Number of requests waiting for their approval
- Workflow steps where they're approvers (Manager, ResourceOwner, SystemOwner)
- Comparison of approval responsibilities
- Backlog analysis

**Current Access Comparison:**
- Common resources (both have access)
- Unique to User 1
- Unique to User 2
- Compliance differences
- Violation comparisons

**Assignment Policy Comparison:**
- Policies affecting both users
- Policies unique to each user
- Automatic vs manual assignments

**Step 5: Analysis Summary**
I'll highlight:
- Key similarities in roles and responsibilities
- Key differences in access levels
- Potential access anomalies or inconsistencies
- Compliance concerns and violations
- Access request patterns and trends
- Approval workload differences (if applicable)
- Recommendations for access alignment or remediation

**Use Cases for Identity Comparison:**
- Access certification and review
- Role model creation (using an exemplar user)
- Onboarding (matching new user to existing peer)
- Offboarding verification (ensuring access is removed)
- Access anomaly detection (peer comparison)
- Privilege analysis (comparing similar roles)
- Approval workload balancing (comparing approver responsibilities)
- Access request audit (reviewing request patterns)

**Important Notes:**
- Uses UId field (32-char GUID) for identity lookups
- Retrieves full organizational context
- Compares actual assignments (not just roles)
- Includes compliance and violation data
- Compares active access requests and their statuses
- Compares pending approvals if users are approvers
- Uses get_access_requests with identity filtering
- Uses get_pending_approvals to check approval queues

**Ready to compare?** Please provide:
1. Identifying information for the first user (name, email, or employee ID)
2. Identifying information for the second user (name, email, or employee ID)
"""

    logger.info(
        "Registered 11 MCP prompts: request_access_workflow, approve_requests_workflow, "
        "search_identity_workflow, review_assignments_workflow, authentication_workflow, "
        "troubleshooting_workflow, bulk_access_request_workflow, compliance_audit_workflow, "
        "resource_discovery_workflow, identity_context_workflow, compare_identities_workflow"
    )
