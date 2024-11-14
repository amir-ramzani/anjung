import base64
import json
from typing import Any

import boto3
import pytest
from app.lambdas.scan_result.handler import on_object_created, on_scan_result
from fastapi.testclient import TestClient

from tests.conftest import REGION, Org, World

BUCKET = "anjung-docs-test"


def s3() -> Any:
    return boto3.client("s3", region_name=REGION)


def policy(upload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(base64.b64decode(upload["fields"]["policy"]))


def request_upload(client: TestClient, headers: dict[str, str], **over: Any):  # type: ignore[no-untyped-def]
    body = {
        "owner_type": "tenant",
        "owner_id": "x",
        "type": "other",
        "filename": "a.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1000,
        **over,
    }
    return client.post("/v1/documents/upload-url", headers=headers, json=body)


def upload_and_scan(world: World, org: Org, doc_id: str, verdict: str = "clean") -> str:
    doc = world.c.store.documents(org.id).get(doc_id)
    assert doc is not None
    s3().put_object(Bucket=BUCKET, Key=doc["s3_key"], Body=b"%PDF-1.4 test")
    assert on_object_created(world.c.store.table, doc["s3_key"], world.c.clock) == "scanning"
    return on_scan_result(
        world.c.store.table, world.c.storage, doc["s3_key"], verdict, world.c.clock
    )  # type: ignore[arg-type]


def test_upload_policy_is_tight(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    r = request_upload(
        client,
        org.admin,
        owner_id=t["id"],
        type="identity",
        filename="../../my id (1).pdf",
        size_bytes=5000,
    )
    assert r.status_code == 201
    body = r.json()
    up = body["upload"]
    pol = policy(up)
    key = up["fields"]["key"]
    assert key == f"org/{org.id}/tenant/{t['id']}/{body['document_id']}/my_id_1_.pdf"
    conds = pol["conditions"]
    assert {"Content-Type": "application/pdf"} in conds
    assert ["content-length-range", 1, 5000] in conds  # S3 itself rejects anything bigger
    assert {"key": key} in conds and {"bucket": BUCKET} in conds
    assert up["method"] == "POST" and up["expires_at"].startswith("2018-11-20T03:10:00")
    doc = client.get(f"/v1/documents/{body['document_id']}", headers=org.admin).json()
    assert doc["status"] == "pending_upload" and doc["retention_class"] == "lease_financial"
    assert doc["filename"] == "my_id_1_.pdf" and "s3_key" not in doc


def test_upload_validation_and_ownership(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    for over in (
        {"size_bytes": 15728641},
        {"size_bytes": 0},
        {"content_type": "text/html"},
        {"type": "contract"},
        {"filename": ""},
        {"owner_id": "short"},
    ):
        args = {"owner_id": t["id"], **over}
        assert request_upload(client, org.admin, **args).status_code == 422, over
    assert (
        request_upload(client, org.admin, owner_id="01JD8Z3K9Q4M7T2V6X8B5N1C0B").status_code == 404
    )
    r = request_upload(client, org.admin, owner_type="invoice", owner_id=t["id"])
    assert r.status_code == 404  # a tenant id is not an invoice id


def test_tenant_upload_rules(world: World, client: TestClient) -> None:
    org = world.make_org()
    p = org.new_property()
    unit = org.new_unit(p["id"])
    me, other = org.new_tenant(), org.new_tenant()
    lease = org.new_lease(unit["id"], [me["id"]])
    tok = org.tenant_login(me)
    ok = request_upload(client, tok, owner_id=me["id"], type="identity")
    assert ok.status_code == 201
    assert request_upload(client, tok, owner_id=other["id"], type="identity").status_code == 403
    assert request_upload(client, tok, owner_id=unit["id"], owner_type="unit").status_code == 403
    assert request_upload(client, tok, owner_id=me["id"], type="lease_agreement").status_code == 403
    assert (
        request_upload(client, tok, owner_type="lease", owner_id=lease["id"]).status_code == 403
    )  # still a draft
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    assert request_upload(client, tok, owner_type="lease", owner_id=lease["id"]).status_code == 201


def test_scan_lifecycle_and_download_gate(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    did = request_upload(client, org.admin, owner_id=t["id"], type="identity").json()["document_id"]
    r = client.get(f"/v1/documents/{did}/download-url", headers=org.admin)
    assert r.status_code == 409 and r.json()["code"] == "document_not_clean"
    assert upload_and_scan(world, org, did) == "clean"
    assert client.get(f"/v1/documents/{did}", headers=org.admin).json()["status"] == "clean"
    r = client.get(f"/v1/documents/{did}/download-url", headers=org.admin)
    assert r.status_code == 200
    dl = r.json()
    assert (
        dl["expires_at"].startswith("2018-11-20T03:05:00")
        and "response-content-disposition" in dl["url"].lower()
    )
    assert dl["filename"] == "a.pdf" and "ResponseContentDisposition" not in dl["url"]
    key = world.c.store.documents(org.id).get(did)["s3_key"]  # type: ignore[index]
    assert {"Key": "scan", "Value": "clean"} in s3().get_object_tagging(Bucket=BUCKET, Key=key)[
        "TagSet"
    ]


def test_infected_files_are_quarantined_and_blocked(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    did = request_upload(client, org.admin, owner_id=t["id"]).json()["document_id"]
    assert upload_and_scan(world, org, did, "infected") == "infected"
    assert client.get(f"/v1/documents/{did}", headers=org.admin).json()["status"] == "infected"
    assert client.get(f"/v1/documents/{did}/download-url", headers=org.admin).status_code == 409
    doc = world.c.store.documents(org.id).get(did)
    assert doc is not None and doc["s3_key"].startswith("quarantine/org/")
    keys = [o["Key"] for o in s3().list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert keys == [doc["s3_key"]]  # the original path is gone


def test_scan_handlers_ignore_foreign_and_repeated_events(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    did = request_upload(client, org.admin, owner_id=t["id"]).json()["document_id"]
    t_ = world.c.store.table
    assert on_object_created(t_, "random/key.pdf", world.c.clock) == "ignored"
    assert (
        on_scan_result(
            t_,
            world.c.storage,
            f"org/{org.id}/tenant/{t['id']}/{did}/other.pdf",
            "clean",
            world.c.clock,
        )
        == "ignored"
    )  # type: ignore[arg-type]
    assert upload_and_scan(world, org, did) == "clean"
    key = world.c.store.documents(org.id).get(did)["s3_key"]  # type: ignore[index]
    assert on_scan_result(t_, world.c.storage, key, "infected", world.c.clock) == "ignored"  # type: ignore[arg-type]
    assert client.get(f"/v1/documents/{did}", headers=org.admin).json()["status"] == "clean"


def test_tenant_access_to_documents(world: World, client: TestClient) -> None:
    org = world.make_org()
    me, other = org.new_tenant(), org.new_tenant()
    tok = org.tenant_login(me)
    mine = request_upload(client, tok, owner_id=me["id"]).json()["document_id"]
    theirs = request_upload(client, org.admin, owner_id=other["id"]).json()["document_id"]
    upload_and_scan(world, org, mine)
    assert client.get(f"/v1/documents/{mine}/download-url", headers=tok).status_code == 200
    assert client.get(f"/v1/documents/{theirs}", headers=tok).status_code == 403
    assert client.get(f"/v1/documents/{theirs}/download-url", headers=tok).status_code == 403
    assert client.delete(f"/v1/documents/{mine}", headers=tok).status_code == 403


def test_delete_and_retention_hold(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    held = request_upload(client, org.admin, owner_id=t["id"], type="identity").json()[
        "document_id"
    ]
    r = client.delete(f"/v1/documents/{held}", headers=org.admin)
    assert r.status_code == 409
    free = request_upload(client, org.admin, owner_id=t["id"], type="other").json()["document_id"]
    upload_and_scan(world, org, free)
    key = world.c.store.documents(org.id).get(free)["s3_key"]  # type: ignore[index]
    assert client.delete(f"/v1/documents/{free}", headers=org.admin).status_code == 204
    assert client.get(f"/v1/documents/{free}", headers=org.admin).status_code == 404
    with pytest.raises(Exception):  # noqa: B017, PT011
        s3().head_object(Bucket=BUCKET, Key=key)


def test_documents_of_a_tenant_are_in_the_subject_index(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    did = request_upload(client, org.admin, owner_id=t["id"]).json()["document_id"]
    items, _ = world.c.store.table.query(pk=f"ORG#{org.id}#SUBJECT#{t['id']}", index="GSI5")
    assert any(i["SK"] == f"DOC#{did}" for i in items)


def test_storage_not_configured(world: World, client: TestClient) -> None:
    org = world.make_org()
    t = org.new_tenant()
    world.c.storage = None
    assert request_upload(client, org.admin, owner_id=t["id"]).status_code == 503


def test_list_documents_of_an_owner_newest_first_with_paging(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    t = org.new_tenant()
    ids = [
        request_upload(client, org.admin, owner_id=t["id"], filename=f"f{i}.pdf").json()[
            "document_id"
        ]
        for i in range(3)
    ]
    other = org.new_tenant()
    request_upload(
        client, org.admin, owner_id=other["id"]
    )  # someone else's document must not show up
    r = client.get(
        "/v1/documents",
        headers=org.admin,
        params={"owner_type": "tenant", "owner_id": t["id"], "limit": 2},
    )
    assert r.status_code == 200
    page = r.json()
    assert [d["id"] for d in page["items"]] == [ids[2], ids[1]] and page["next_cursor"]
    assert "s3_key" not in page["items"][0] and page["items"][0]["status"] == "pending_upload"
    nxt = client.get(
        "/v1/documents",
        headers=org.admin,
        params={
            "owner_type": "tenant",
            "owner_id": t["id"],
            "limit": 2,
            "cursor": page["next_cursor"],
        },
    ).json()
    assert [d["id"] for d in nxt["items"]] == [ids[0]] and nxt["next_cursor"] is None
    # a cursor cannot be moved to another owner
    bad = client.get(
        "/v1/documents",
        headers=org.admin,
        params={"owner_type": "tenant", "owner_id": other["id"], "cursor": page["next_cursor"]},
    )
    assert bad.status_code == 400


def test_list_documents_access_rules(world: World, client: TestClient) -> None:
    org = world.make_org()
    me, other = org.new_tenant(), org.new_tenant()
    tok = org.tenant_login(me)
    request_upload(client, org.admin, owner_id=other["id"])
    mine = request_upload(client, tok, owner_id=me["id"]).json()["document_id"]
    q = {"owner_type": "tenant", "owner_id": me["id"]}
    assert [
        d["id"] for d in client.get("/v1/documents", headers=tok, params=q).json()["items"]
    ] == [mine]
    assert (
        client.get("/v1/documents", headers=tok, params={**q, "owner_id": other["id"]}).status_code
        == 403
    )
    unit = org.new_unit(org.new_property()["id"])
    assert (
        client.get(
            "/v1/documents", headers=tok, params={"owner_type": "unit", "owner_id": unit["id"]}
        ).status_code
        == 403
    )
    foreign = world.make_org()
    ft = foreign.new_tenant()
    assert (
        client.get(
            "/v1/documents", headers=org.admin, params={**q, "owner_id": ft["id"]}
        ).status_code
        == 404
    )
    for bad in (
        {"owner_type": "tenant"},
        {"owner_id": me["id"]},
        {**q, "owner_type": "spaceship"},
        {**q, "owner_id": "x"},
    ):
        assert client.get("/v1/documents", headers=org.admin, params=bad).status_code == 422, bad
    assert client.get("/v1/documents", params=q).status_code == 401


def test_a_tenant_lists_documents_of_their_own_lease_only_once_it_is_not_a_draft(
    world: World, client: TestClient
) -> None:
    org = world.make_org()
    t = org.new_tenant()
    tok = org.tenant_login(t)
    lease = org.new_lease(org.new_unit(org.new_property()["id"])["id"], [t["id"]])
    request_upload(
        client, org.admin, owner_type="lease", owner_id=lease["id"], type="lease_agreement"
    )
    q = {"owner_type": "lease", "owner_id": lease["id"]}
    assert (
        client.get("/v1/documents", headers=tok, params=q).status_code == 403
    )  # a draft is internal
    client.post(f"/v1/leases/{lease['id']}/activate", headers=org.admin)
    assert len(client.get("/v1/documents", headers=tok, params=q).json()["items"]) == 1
