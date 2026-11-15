import os

# Must be set before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["APP_ENV"] = "test"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
