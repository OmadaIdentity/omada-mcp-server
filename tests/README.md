# Tests

These are integration tests that run against a live Omada instance.

## Setup

1. Ensure `.env` is configured with `OMADA_BASE_URL`, `TENANT_ID`, and `CLIENT_ID`.
2. Run the server once manually to complete browser authentication and cache the token.

## Running tests

```bash
# From the repo root
pip install pytest pytest-asyncio
python -m pytest
```

Tests marked `live_api` are skipped automatically if `OMADA_BASE_URL` is not set.
`test_ping` always runs — no configuration needed.

## Optional env vars for user-specific tests

Add these to your `.env` to enable tests that target a specific identity:

```bash
TEST_USER_EMAIL=user@yourdomain.com                          # valid UPN in your Omada instance
TEST_USER_ID=12345                                           # integer OData Id of the same user
TEST_USER_IDENTITY_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  # UId GUID for GraphQL tests
```

## Test files

| File | What it tests |
|---|---|
| `test_admin.py` | `ping` (no auth), `check_omada_config`, `get_cache_stats` |
| `test_query.py` | Identity and entity OData queries, counts, filters |
| `test_access_requests.py` | Access request queries, identity contexts |
| `test_assignments.py` | Calculated assignments, compliance workbench |
