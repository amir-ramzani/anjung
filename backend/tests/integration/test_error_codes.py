"""Machine-readable codes for field issues and delete blockers (contract v0.16.0), so the SPA can show
them in the user's language. `issue` and `message` stay the English fallback."""

from app.core.errors import ValidationFailed, issue_code
from fastapi.testclient import TestClient

from tests.conftest import World

ADDR = {"line1": "1 Jalan A", "city": "KL", "state": "WP", "postcode": "50000"}


def details(r: object) -> dict[str, dict]:  # type: ignore[type-arg]
    assert r.status_code == 422, r.text  # type: ignore[attr-defined]
    return {d["field"]: d for d in r.json()["details"]}  # type: ignore[attr-defined]


def test_validator_errors_carry_their_type_and_limits(world: World, client: TestClient) -> None:
    org = world.make_org()
    d = details(
        client.post("/v1/tenants", headers=org.admin, json={"full_name": "", "email": "nope"})
    )
    assert d["full_name"]["code"] == "string_too_short" and d["full_name"]["params"] == {
        "min_length": 1
    }
    assert d["email"]["code"] == "invalid_email" and "issue" in d["email"]
    d = details(client.post("/v1/tenants", headers=org.admin, json={"email": "a@example.com"}))
    assert d["full_name"]["code"] == "missing" and "params" not in d["full_name"]


def test_fixed_issues_get_a_code_made_from_their_text(world: World, client: TestClient) -> None:
    org = world.make_org()
    d = details(client.get("/v1/reports/income?from=2019-02&to=2019-01", headers=org.admin))
    assert d["to"] == {
        "field": "to",
        "issue": "must not be before from",
        "code": "must_not_be_before_from",
    }
    assert issue_code("The bay's unit is not available") == "the_bay_s_unit_is_not_available"
    explicit = ValidationFailed(
        details=[{"field": "x", "issue": "needs 3 days", "code": "notice_too_short"}]
    )
    assert (
        explicit.details and explicit.details[0]["code"] == "notice_too_short"
    )  # never overwritten


def test_a_validator_message_becomes_a_code(world: World, client: TestClient) -> None:
    org = world.make_org()
    r = client.post(
        "/v1/users/invite",
        headers=org.admin,
        json={"email": "x@example.com", "roles": ["property_manager", "property_manager"]},
    )
    assert any(d["code"] == "roles_must_be_unique" for d in r.json()["details"]), r.text


def test_blockers_carry_the_values_in_their_message(world: World, client: TestClient) -> None:
    org = world.make_org()
    prop = client.post(
        "/v1/properties",
        headers=org.admin,
        json={"name": "P", "type": "landed", "address": ADDR},
    ).json()
    unit = org.new_unit(prop["id"])
    for _ in range(2):
        client.post(
            "/v1/maintenance-requests",
            headers=org.admin,
            json={"unit_id": unit["id"], "title": "t", "description": "d", "category": "other"},
        ).raise_for_status()
    preview = client.get(f"/v1/properties/{prop['id']}/deletion", headers=org.admin).json()
    [b] = preview["blockers"]
    assert b["code"] == "maintenance_history" and b["params"] == {"what": "property", "count": 2}
    assert "2 maintenance request" in b["message"]
