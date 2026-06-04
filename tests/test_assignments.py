"""
Assignment and compliance tool tests — require a live Omada instance.

Optional env vars:
  TEST_USER_IDENTITY_ID  — the UId (GUID) of a user in your Omada instance
"""
import json
import os
import pytest


@pytest.mark.live_api
async def test_compliance_workbench_config():
    from tools.assignments import get_compliance_workbench_survey_and_compliance_status
    result = await get_compliance_workbench_survey_and_compliance_status()
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_get_calculated_assignments_detailed():
    identity_id = os.getenv("TEST_USER_IDENTITY_ID")
    if not identity_id:
        pytest.skip("Set TEST_USER_IDENTITY_ID in .env to run this test")
    from tools.assignments import get_calculated_assignments_detailed
    result = await get_calculated_assignments_detailed(
        identity_ids=identity_id,
        rows=5,
    )
    data = json.loads(result)
    assert data["status"] == "success"


@pytest.mark.live_api
async def test_get_calculated_assignments_with_filter():
    identity_id = os.getenv("TEST_USER_IDENTITY_ID")
    if not identity_id:
        pytest.skip("Set TEST_USER_IDENTITY_ID in .env to run this test")
    from tools.assignments import get_calculated_assignments_detailed
    result = await get_calculated_assignments_detailed(
        identity_ids=identity_id,
        compliance_status="APPROVED",
        rows=5,
    )
    data = json.loads(result)
    assert data["status"] == "success"
