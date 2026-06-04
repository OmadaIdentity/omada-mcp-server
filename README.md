# Omada Identity MCP Server

> **Important:** This MCP server is published as a reference project from our innovation initiative.
> It is made available free of charge for evaluation and exploration, but is not part of Omada's
> supported production toolset. Functionality and APIs may evolve, and it is not intended for
> mission-critical workloads. See [LICENSE](LICENSE) for terms of use.
> If you encounter issues or would like to share feedback, please reach out through our
> [support channel on Freshdesk](https://omadasupport.freshservice.com/a/tickets/new).

A [Model Context Protocol](https://modelcontextprotocol.io) server that connects Claude Desktop, GitHub Copilot, and other MCP clients directly to an Omada Identity Governance instance.

Authentication is fully automatic — OAuth 2.0 Authorization Code + PKCE opens the browser once, then silently refreshes in the background. No bearer tokens, no separate auth server, no manual copy-paste.

## Features

- **Automatic authentication** — Auth Code + PKCE, token encrypted at rest with Windows DPAPI
- **OData API** — query identities, resources, roles, assignments with full filtering and pagination
- **GraphQL API** — access requests, approvals, contexts, calculated assignments, compliance
- **Works in Claude Desktop and GitHub Copilot** — stdio transport, no extra setup
- **Response caching** — configurable TTL to reduce repeated API calls

## Start here

New to this project? Follow these three steps for your first successful interaction:

1. **[Azure AD setup](#azure-ad-setup)** — create a dedicated app registration (5 min, requires Azure admin for consent)
2. **[Configuration](#configuration)** — add three values to your MCP client config (no `.env` needed)
3. **[MCP client config](#mcp-client-configuration)** — point Claude Desktop or Copilot at the server

On first use the server opens a browser for login, then calls `ping` to confirm connectivity. After that, try:
> *"Show me the first 5 identities in Omada"*

If you get stuck, reach out through the [Omada support channel on Freshdesk](https://omadasupport.freshservice.com/a/tickets/new).

## Requirements

- Python 3.10+
- An Omada Identity Cloud instance with OpenID authentication configured
- An Azure AD app registration (public client — no secret required)
- The user account must exist in the Omada environment

## Installation

```bash
git clone <your-repo-url>
cd omada-mcp-server
pip install -r requirements.txt
```

Configuration is provided via the MCP client config (Claude Desktop or VS Code) — no `.env` file needed for normal use. See [Configuration](#configuration) for details.

## Azure AD setup

There are **two separate app registrations** involved:

- **Omada app** — the app that backs the Omada API. It already exists and is managed by your Omada/IT team. You do not create this one.
- **MCP client app** — a new, dedicated app you create below. This is what the MCP server uses to authenticate users and obtain tokens to call the Omada API.

### Prerequisite: verify the Omada app exposes its API

Before creating the client app, confirm that the Omada app registration (the one that backs your Omada instance) has **Expose an API** configured with a `user_impersonation` scope.

**How to find the Omada app registration:** go to **Azure Portal → App registrations → All applications** and look for an app whose name matches your Omada instance (e.g. `https://your-instance.omada.cloud/logon.aspx`). If you are unsure which one it is, ask your Omada platform administrator.

**If `user_impersonation` scope does not exist**, create it:

1. Open the Omada app registration → **Expose an API**.
2. If **Application ID URI** is empty, click **Add**. Azure proposes `api://<client-id>` — **replace it** with your Omada base URL: `https://your-instance.omada.cloud`. This is required for authentication to work without extra configuration.
3. Click **+ Add a scope** and fill in:
   - **Scope name**: `user_impersonation`
   - **Who can consent**: Admins only
   - **Admin consent display name**: `Access Omada Identity`
   - **Admin consent description**: `Allows the app to access Omada Identity on behalf of the signed-in user`
   - **State**: Enabled
4. Save.

### Create the MCP client app

1. Go to **Azure Portal → App registrations → New registration**.
   - **Name**: something descriptive, e.g. `omada-mcp-client`.
   - **Supported account types**: select **Accounts in this organizational directory only (Single tenant)**.
   - Leave redirect URI blank for now → **Register**.

2. Go to **Authentication** (the portal may show this as *Authentication (Preview)*).
   - Open the **Redirect URI configuration** tab → click **Add Redirect URI**.
   - Set platform type to **Mobile and desktop applications** and enter `http://localhost:8765` → Save.

3. Still in **Authentication**, open the **Settings** tab.
   - Toggle **Allow public client flows** to **Enabled** → Save.

4. Go to **API permissions → Add a permission → APIs my organization uses**.
   Search for the Omada app by name. If it does not appear in the list, paste its **Application (client) ID** directly into the search box — this always works. Choose **Delegated permissions** and select `user_impersonation` → **Add permissions**.

5. Click **Grant admin consent** for your tenant. This is required because the Omada scope is restricted to admins-only consent. Both permissions (`user_impersonation` and `User.Read`) should show **Granted** status after this step.

6. *(Optional but recommended)* In the **Omada** app registration → **Expose an API → Authorized client applications**, click **Add a client application**, paste the **Client ID** of the MCP client app, and select the `user_impersonation` scope. This pre-authorizes the client and prevents users from seeing a consent popup on first login.

7. Go to the **Overview** page of the MCP client app and copy the **Application (client) ID** and **Directory (tenant) ID** into your `.env` file.


<details>
<summary>Automate with Azure CLI</summary>

All of the above can be scripted with `az cli`. Example to create the app and add the redirect URI:

```bash
# Create the app registration
az ad app create --display-name "omada-mcp-client" --public-client-redirect-uris "http://localhost:8765"

# Enable public client flows (fetch the app's object ID first)
OBJECT_ID=$(az ad app show --id <client-id> --query id -o tsv)
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --body '{"isFallbackPublicClient": true}'

# Add delegated API permission (requires the Omada app's client ID)
az ad app permission add \
  --id <mcp-client-id> \
  --api <omada-app-client-id> \
  --api-permissions <user_impersonation-scope-id>=Scope

# Grant admin consent
az ad app permission admin-consent --id <mcp-client-id>
```

</details>

## Configuration

There are two ways to provide configuration — use whichever fits your setup:

**Option A — `env` block in the MCP client config (recommended)**
Pass the variables directly in `claude_desktop_config.json` or `.vscode/mcp.json` (see [MCP client configuration](#mcp-client-configuration)). No `.env` file needed.

**Option B — `.env` file**
Useful when running `server.py` directly from the terminal or when running tests. Copy `.env.example` to `.env` and fill in your values:

```bash
OMADA_BASE_URL=https://your-instance.omada.cloud
TENANT_ID=your-azure-ad-tenant-id
CLIENT_ID=your-mcp-client-app-registration-client-id  # the MCP client app, NOT the Omada app
```

Only three variables are required. Everything else has a sensible default — the callback port defaults to 8765, GraphQL version to 3.0, caching is on. See `.env.example` for the full list of optional overrides.

## MCP client configuration

### Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "omada": {
      "command": "python",
      "args": ["C:\\path\\to\\omada-mcp-server\\server.py"],
      "env": {
        "OMADA_BASE_URL": "https://your-instance.omada.cloud",
        "TENANT_ID": "your-azure-ad-tenant-id",
        "CLIENT_ID": "your-mcp-client-app-registration-client-id"
      }
    }
  }
}
```

### GitHub Copilot (VS Code)

Add to `.vscode/mcp.json` or user-level MCP config:

```json
{
  "servers": {
    "omada": {
      "type": "stdio",
      "command": "python",
      "args": ["C:\\path\\to\\omada-mcp-server\\server.py"],
      "env": {
        "OMADA_BASE_URL": "https://your-instance.omada.cloud",
        "TENANT_ID": "your-azure-ad-tenant-id",
        "CLIENT_ID": "your-mcp-client-app-registration-client-id"
      }
    }
  }
}
```

## First run

The first time the server is called, a browser window opens for login. After authenticating, the token is saved encrypted to `.token_cache.bin` (Windows DPAPI — only your Windows user can read it). Subsequent starts use the refresh token silently — no browser needed.

To force re-authentication, call the `logout` tool (or delete `.token_cache.bin` manually).

## Available tools

### Identity & resource queries (OData)

| Tool | Description |
|---|---|
| `query_omada_identity` | Query identities with field filters |
| `query_omada_resources` | Query resources by type and system |
| `query_omada_entities` | Generic query for any Omada entity type |
| `query_omada_entity` | Advanced OData query with full filter/select/expand support |
| `get_all_omada_identities` | Retrieve all identities with pagination |
| `query_calculated_assignments` | Query calculated assignments for an identity |

### Access requests (GraphQL)

| Tool | Description |
|---|---|
| `get_access_requests` | List access requests with optional filters |
| `create_access_request` | Submit a new access request |
| `get_resources_for_beneficiary` | Resources available for a specific identity |
| `get_requestable_resources` | Alias for `get_resources_for_beneficiary` |
| `get_identities_for_beneficiary` | Identities available as beneficiaries |
| `get_identity_contexts` | Contexts assigned to a specific identity |

### Approvals (GraphQL)

| Tool | Description |
|---|---|
| `get_pending_approvals` | List pending approval items (summary view) |
| `get_approval_details` | Full approval details including technical IDs |
| `make_approval_decision` | Approve or reject a pending item |

### Assignments & compliance (GraphQL)

| Tool | Description |
|---|---|
| `get_calculated_assignments_detailed` | Detailed assignments with compliance status |
| `get_compliance_workbench_survey_and_compliance_status` | Compliance workbench data |

### Admin

| Tool | Description |
|---|---|
| `ping` | Health check |
| `check_omada_config` | Show current server configuration |
| `logout` | Clear stored tokens and force re-authentication on next call |
| `get_cache_stats` | Cache hit/miss statistics |
| `clear_cache` | Clear response cache |
| `view_cache_contents` | List cached entries |
| `view_cache_contents_detailed` | Detailed cache inspection |
| `get_cache_efficiency` | Cache efficiency metrics |

## Project structure

```
omada-mcp-server/
├── server.py            # Entry point — registers tools and starts server
├── auth.py              # OAuth 2.0 Auth Code + PKCE, DPAPI token cache
├── mcp_instance.py      # Shared FastMCP instance
├── exceptions.py        # Custom exception classes
├── logging_config.py    # Logging setup and per-function log level decorator
├── helpers.py           # Response builders and validation utilities
├── cache.py             # SQLite response cache (OmadaCache class)
├── cache_instance.py    # Shared cache singleton (import cache/CACHE_ENABLED from here)
├── cache_config.py      # Per-operation TTL configuration
├── completions.py       # MCP autocomplete suggestions
├── prompts.py           # MCP workflow prompt templates
├── api/
│   ├── odata.py         # OData client (filters, pagination, entity queries)
│   └── graphql.py       # GraphQL client (request builder, cache integration)
└── tools/
    ├── query.py          # OData query tools
    ├── access_requests.py # Access request tools
    ├── approvals.py      # Approval workflow tools
    ├── assignments.py    # Assignment and compliance tools
    └── admin.py          # Admin and cache management tools
```

## Security

- **No client secret** — PKCE public client flow, nothing to leak
- **Token encrypted at rest** — on Windows, DPAPI ties encryption to your Windows user session; on macOS/Linux, the token cache falls back to a plain JSON file (`.token_cache.bin`) — ensure the file permissions on your system restrict access appropriately
- **Token never in LLM context** — authentication is invisible to the AI model
- **Token never in tool responses** — Bearer token is stripped before results are returned to the model
- **`.env` and `.token_cache.bin` excluded from git** via `.gitignore`

## API endpoints

| API | Endpoint |
|---|---|
| OData (built-in) | `{OMADA_BASE_URL}/OData/BuiltIn/{EntityType}` |
| OData (data objects) | `{OMADA_BASE_URL}/OData/DataObjects/{EntityType}` |
| GraphQL | `{OMADA_BASE_URL}/api/Domain/{version}` |

## Logging

Logs go to both console and `omada_mcp_server.log`. Override log level per tool via environment variables:

```bash
LOG_LEVEL_get_pending_approvals=DEBUG
LOG_LEVEL_create_access_request=DEBUG
```

## Dependency licenses

All direct runtime dependencies use permissive licenses compatible with evaluation use:

| Package | License |
|---|---|
| `httpx` | BSD-3-Clause |
| `fastmcp` | Apache-2.0 |
| `mcp` | MIT |
| `python-dotenv` | BSD-3-Clause |
| `pywin32` *(optional, Windows only)* | PSF |

For commercial use or redistribution, verify transitive dependency licenses independently.
