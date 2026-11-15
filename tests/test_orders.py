import pytest

from tests.conftest import VALID_ORDER


def test_order_crud_lifecycle(client, auth_headers):
    h = auth_headers()
    r = client.post("/orders", json=VALID_ORDER, headers=h)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "complete"
    assert order["source"] == "manual"
    assert order["validation_issues"] == []
    oid = order["id"]

    assert client.get(f"/orders/{oid}", headers=h).json()["patient_last_name"] == "Doe"
    assert [o["id"] for o in client.get("/orders", headers=h).json()] == [oid]

    r = client.patch(f"/orders/{oid}", json={"patient_last_name": "  Doe-Smith "}, headers=h)
    assert r.status_code == 200
    assert r.json()["patient_last_name"] == "Doe-Smith"

    assert client.delete(f"/orders/{oid}", headers=h).status_code == 204
    assert client.get(f"/orders/{oid}", headers=h).status_code == 404


def test_users_cannot_see_or_modify_each_others_orders(client, auth_headers):
    alice = auth_headers("alice@example.com")
    bob = auth_headers("bob@example.com")
    oid = client.post("/orders", json=VALID_ORDER, headers=alice).json()["id"]

    assert client.get("/orders", headers=bob).json() == []
    assert client.get(f"/orders/{oid}", headers=bob).status_code == 404
    assert client.patch(f"/orders/{oid}", json={"patient_first_name": "Eve"}, headers=bob).status_code == 404
    assert client.delete(f"/orders/{oid}", headers=bob).status_code == 404

    # Alice's order is untouched.
    r = client.get(f"/orders/{oid}", headers=alice)
    assert r.status_code == 200
    assert r.json()["patient_first_name"] == "Jane"


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_npi", "1234567890"),  # bad check digit
        ("provider_npi", "12345"),
        ("provider_npi", "12345678a3"),
        ("patient_dob", "2999-01-01"),
        ("patient_dob", "1850-01-01"),
        ("patient_dob", "not-a-date"),
        ("patient_first_name", "   "),
        ("patient_last_name", "D0e"),
        ("provider_name", "x" * 101),
    ],
)
def test_create_rejects_invalid_fields(client, auth_headers, field, value):
    r = client.post("/orders", json={**VALID_ORDER, field: value}, headers=auth_headers())
    assert r.status_code == 422


def test_create_requires_all_fields(client, auth_headers):
    body = {k: v for k, v in VALID_ORDER.items() if k != "provider_npi"}
    assert client.post("/orders", json=body, headers=auth_headers()).status_code == 422


def test_patch_rejects_nulls_unknown_fields_and_invalid_values(client, auth_headers):
    h = auth_headers()
    oid = client.post("/orders", json=VALID_ORDER, headers=h).json()["id"]
    assert client.patch(f"/orders/{oid}", json={"provider_npi": None}, headers=h).status_code == 422
    assert client.patch(f"/orders/{oid}", json={"owner_id": 999}, headers=h).status_code == 422
    assert client.patch(f"/orders/{oid}", json={"provider_npi": "1234567890"}, headers=h).status_code == 422


def test_list_pagination(client, auth_headers):
    h = auth_headers()
    ids = [client.post("/orders", json=VALID_ORDER, headers=h).json()["id"] for _ in range(3)]
    page = client.get("/orders?limit=2&offset=1", headers=h).json()
    assert [o["id"] for o in page] == sorted(ids, reverse=True)[1:3]
