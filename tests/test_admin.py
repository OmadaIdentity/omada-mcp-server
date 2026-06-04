"""
Admin tool tests.
test_ping runs without any configuration.
All other tests require a live Omada instance — see README.md.
"""
import json
import pytest


def test_ping():
    from tools.admin import ping
    result = ping()
    assert "pong" in result.lower()


@pytest.mark.live_api
async def test_check_config():
    from tools.admin import check_omada_config
    result = await check_omada_config()
    data = json.loads(result)
    assert data["status"] == "success"
    assert "omada_base_url" in data.get("data", {})


@pytest.mark.live_api
async def test_cache_stats():
    from tools.admin import get_cache_stats
    result = await get_cache_stats()
    data = json.loads(result)
    assert data["status"] == "success"
