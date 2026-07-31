import os
from pathlib import Path

import pytest


TEST_DB = Path(__file__).resolve().parent / "manager-test.db"
TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["MANAGER_ACCESS_KEY"] = "manager-test-access"
os.environ["ADOBE2API_OPS_KEY"] = "manager-test-ops"
os.environ["MANAGER_COOKIE_SECURE"] = "false"
os.environ["MANAGER_AUTO_MIGRATE"] = "false"
os.environ["POLL_INTERVAL_SECONDS"] = "3600"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.database import SessionLocal, create_schema  # noqa: E402
from app.main import app  # noqa: E402
from app.security import _login_attempts  # noqa: E402
from app.safe_replacements import safe_replacement_operations  # noqa: E402
from app.auto_replacements import auto_replacement_service  # noqa: E402
from app.replacement_coordinator import replacement_coordinator  # noqa: E402
from app.models import (  # noqa: E402
    AlertEvent,
    AlertRule,
    AlertSilence,
    AuditEvent,
    Instance,
    ManagerSetting,
    MetricSample,
)


@pytest.fixture(autouse=True)
def clean_database():
    _login_attempts.clear()
    safe_replacement_operations.clear()
    auto_replacement_service.clear()
    replacement_coordinator.clear()
    create_schema()
    with SessionLocal() as db:
        for model in (
            AlertSilence,
            AlertEvent,
            MetricSample,
            AuditEvent,
            Instance,
            AlertRule,
            ManagerSetting,
        ):
            db.execute(delete(model))
        db.commit()
    yield
    safe_replacement_operations.clear()
    auto_replacement_service.clear()
    replacement_coordinator.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authenticated(client):
    response = client.post("/api/auth/login", json={"access_key": "manager-test-access"})
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    return client, {"X-CSRF-Token": csrf}
