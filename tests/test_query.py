"""
OData query tool tests — require a live Omada instance.

Optional env vars for user-specific tests:
  TEST_USER_EMAIL     — a valid user UPN/email in your Omada instance
  TEST_USER_ID        — the integer OData Id of the same user
"""
import json
import os
import pytest


@pytest.mark.live_api
async def test_count_identities():
    from tools.query import query_omada_identity
    result = await query_omada_identity(count_only=True)
    data = json.loads(result)
    assert data["status"] == "success"
    assert data.get("count", 0) > 0


@pytest.mark.live_api
async def test_list_identities():
    from tools.query import query_omada_identity
    result = await query_omada_identity(top=5, summary_mode=True)
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_search_identity_by_email():
    email = os.getenv("TEST_USER_EMAIL")
    if not email:
        pytest.skip("Set TEST_USER_EMAIL in .env to run this test")
    from tools.query import query_omada_identity
    result = await query_omada_identity(
        field_filters=[{"field": "EMAIL", "operator": "eq", "value": email}],
        top=1,
    )
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_search_identity_contains():
    email = os.getenv("TEST_USER_EMAIL")
    if not email:
        pytest.skip("Set TEST_USER_EMAIL in .env to run this test")
    domain = email.split("@")[-1]
    from tools.query import query_omada_identity
    result = await query_omada_identity(
        field_filters=[{"field": "EMAIL", "operator": "contains", "value": domain}],
        top=5,
    )
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_count_systems():
    from tools.query import query_omada_entity
    result = await query_omada_entity(entity_type="System", count_only=True)
    data = json.loads(result)
    assert data["status"] == "success"
    assert data.get("count", 0) >= 0


@pytest.mark.live_api
async def test_list_systems():
    from tools.query import query_omada_entity
    result = await query_omada_entity(entity_type="System", top=5)
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_query_calculated_assignments():
    identity_id = os.getenv("TEST_USER_ID")
    if not identity_id:
        pytest.skip("Set TEST_USER_ID in .env to run this test")
    from tools.query import query_calculated_assignments
    result = await query_calculated_assignments(
        identity_id=int(identity_id),
        top=5,
    )
    data = json.loads(result)
    assert data["status"] == "success"
