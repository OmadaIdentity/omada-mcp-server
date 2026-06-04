import os
import pytest
from dotenv import load_dotenv

load_dotenv()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_api: requires a live Omada instance (OMADA_BASE_URL, TENANT_ID, CLIENT_ID in .env)"
    )


def pytest_runtest_setup(item):
    if "live_api" in item.keywords:
        if not os.getenv("OMADA_BASE_URL"):
            pytest.skip("live_api: set OMADA_BASE_URL in .env to run integration tests")
