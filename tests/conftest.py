import os
from pathlib import Path

os.environ["AIOC_ENV"] = "development"
os.environ["AIOC_DATABASE_PATH"] = "/tmp/aioc-pytest.db"
test_db_url = os.getenv("AIOC_TEST_DATABASE_URL", "")
if test_db_url:
    os.environ["AIOC_DATABASE_URL"] = test_db_url
else:
    os.environ.pop("AIOC_DATABASE_URL", None)
os.environ["AIOC_OWNER_TOKEN"] = "test-owner"
os.environ["AIOC_EVENT_TOKEN"] = "test-event"
os.environ["AIOC_MASTER_KEY"] = "test-master-key-for-aioc-tests"
os.environ["AIOC_ALLOWED_CONNECTOR_HOSTS"] = "*"
os.environ["AIOC_AI_PROVIDER"] = "rules"
os.environ["AIOC_CIRCUIT_FAILURE_THRESHOLD"] = "2"

import pytest
from fastapi.testclient import TestClient

from app.core.db import reset_database_for_tests, reset_engine_for_tests
from app.main import app


@pytest.fixture()
def client():
    db = Path("/tmp/aioc-pytest.db")
    reset_engine_for_tests()
    if test_db_url:
        reset_database_for_tests()
    elif db.exists():
        db.unlink()
    with TestClient(app) as c:
        yield c
    if test_db_url:
        reset_database_for_tests()
    reset_engine_for_tests()
    if db.exists(): db.unlink()


@pytest.fixture()
def auth(): return {"X-Owner-Token": "test-owner"}


@pytest.fixture()
def hotel_manifest():
    return {
        "id": "hotel-test", "name": "Hotel Test", "type": "hotel-management", "environment": "production",
        "connector": {"type": "simulator"},
        "capabilities": [
            {"name": "hotel.status", "default_policy": "read"},
            {"name": "shift.close", "default_policy": "controlled"},
            {"name": "transaction.refund", "default_policy": "approval"},
            {"name": "ledger.delete", "default_policy": "forbidden"}
        ]
    }
