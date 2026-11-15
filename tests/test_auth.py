import jwt


def test_register_login_me(client, auth_headers):
    headers = auth_headers("Alice@Example.com")
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"


def test_register_rejects_duplicate_email_case_insensitively(client, auth_headers):
    auth_headers("bob@example.com")
    r = client.post("/auth/register", json={"email": "BOB@example.com", "password": "another-password"})
    assert r.status_code == 409


def test_register_rejects_short_password_and_bad_email(client):
    assert client.post("/auth/register", json={"email": "a@example.com", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"email": "not-an-email", "password": "long-enough"}).status_code == 422


def test_login_rejects_wrong_password_and_unknown_user(client, auth_headers):
    auth_headers("carol@example.com", "right-password")
    r = client.post("/auth/token", data={"username": "carol@example.com", "password": "wrong-password"})
    assert r.status_code == 401
    r = client.post("/auth/token", data={"username": "nobody@example.com", "password": "whatever-pass"})
    assert r.status_code == 401


def test_protected_routes_require_valid_token(client):
    assert client.get("/orders").status_code == 401
    assert client.get("/orders", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    forged = jwt.encode({"sub": "1", "exp": 9999999999}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert client.get("/orders", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_expired_token_rejected(client, auth_headers):
    from app.config import get_settings

    auth_headers("dave@example.com")
    expired = jwt.encode({"sub": "1", "exp": 1}, get_settings().jwt_secret, algorithm="HS256")
    assert client.get("/orders", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
