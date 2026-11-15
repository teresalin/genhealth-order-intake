from app.config import Settings


def test_health_reports_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["database_backend"] == "sqlite"
    assert "commit" in body


def test_postgres_url_is_normalized():
    s = Settings(database_url="postgres://u:p@h:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_production_rejects_default_secret():
    import pytest

    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="change-me")
