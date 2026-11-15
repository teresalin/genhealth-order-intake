import os

# Must be set before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-signing"
os.environ["BCRYPT_ROUNDS"] = "4"
# Tests never call the real model; an empty key overrides any key in a local .env file.
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app

VALID_ORDER = {
    "patient_first_name": "Jane",
    "patient_last_name": "Doe",
    "patient_dob": "1950-04-12",
    "provider_name": "Dr. John Smith, MD",
    "provider_npi": "1234567893",  # CMS documentation example; passes the NPI check digit
}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def auth_headers(client):
    """Returns a function that registers a user and returns its Authorization header."""

    def make(email: str = "alice@example.com", password: str = "correct-horse-battery") -> dict:
        r = client.post("/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        r = client.post("/auth/token", data={"username": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return make
