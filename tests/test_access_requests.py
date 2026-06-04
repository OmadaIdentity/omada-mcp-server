"""
Access request tool tests — require a live Omada instance.

Optional env vars:
  TEST_USER_IDENTITY_ID  — the UId (GUID) of a user in your Omada instance
"""
import json
import os
import pytest


@pytest.mark.live_api
async def test_get_access_requests():
    from tools.access_requests import get_access_requests
    result = await get_access_requests(summary_mode=True)
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_get_access_requests_filter_pending():
    from tools.access_requests import get_access_requests
    result = await get_access_requests(
        filter_field="status",
        filter_value="PENDING",
        summary_mode=True,
    )
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_get_identity_contexts():
    identity_id = os.getenv("TEST_USER_IDENTITY_ID")
    if not identity_id:
        pytest.skip("Set TEST_USER_IDENTITY_ID in .env to run this test")
    from tools.access_requests import get_identity_contexts
    result = await get_identity_contexts(identity_id=identity_id)
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_get_identities_for_beneficiary():
    from tools.access_requests import get_identities_for_beneficiary
    result = await get_identities_for_beneficiary(rows=5)
    data = json.loads(result)
    assert data["status"] == "success"
