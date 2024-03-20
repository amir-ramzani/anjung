"""Dev-only endpoints, mounted only when AUTH_MODE=mock: POST /dev/token and a scan stand-in."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import EmailStr

from app.domains.common import Request as Body
from app.lambdas.scan_result.handler import on_object_created, on_scan_result
from app.repositories import keys

router = APIRouter(prefix="/dev", tags=["Dev"])


class DevTokenRequest(Body):
    email: EmailStr


@router.post("/token")
def dev_token(body: DevTokenRequest, request: Request) -> dict[str, Any]:
    issuer = request.app.state.container.dev_issuer
    return {"access_token": issuer.issue(body.email), "token_type": "Bearer"}


class DevScanRequest(Body):
    org_id: str
    verdict: Literal["clean", "infected"] = "clean"


@router.post("/documents/{document_id}/scan")
def dev_scan(document_id: str, body: DevScanRequest, request: Request) -> dict[str, str]:
    """Stands in for the S3 event plus the malware scanner: run after uploading the file."""
    c = request.app.state.container
    doc = c.store.table.get(keys.org_pk(body.org_id), keys.doc_sk(document_id))
    if doc is None or c.storage is None:
        raise HTTPException(404)
    if c.storage.head(doc["s3_key"]) is None:
        raise HTTPException(409, "the file has not been uploaded yet")
    on_object_created(c.store.table, doc["s3_key"], c.clock)
    return {
        "status": on_scan_result(c.store.table, c.storage, doc["s3_key"], body.verdict, c.clock)
    }


class DevJobRequest(Body):
    org_id: str | None = None


@router.post("/jobs/{job_type}")
def dev_job(job_type: str, body: DevJobRequest, request: Request) -> dict[str, str]:
    """Run a scheduled job now (the EventBridge schedules do this in AWS), e.g. invoice_sweep, overdue_sweep,
    lease_expiry, integrity_check, reconcile_payments, or the *_all fan-outs."""
    from app.worker.envelope import make_envelope

    c = request.app.state.container
    org = body.org_id or c.settings.platform_org_id
    c.jobs.enqueue("housekeeping", make_envelope(job_type, org, {}, c.clock.now()))
    return {"enqueued": job_type}


class DevPayRequest(Body):
    reference: str
    amount_minor: int
    status: Literal["succeeded", "failed", "reversed"] = "succeeded"
    txn_id: str | None = None


@router.post("/pay")
async def dev_pay(body: DevPayRequest, request: Request) -> dict[str, Any]:
    """Stands in for the gateway: sends the same signed webhook the real one would, through the real endpoint."""
    import uuid

    import httpx

    c = request.app.state.container
    prov = c.payments["fake"]
    payload, headers = prov.signed_request(
        reference=body.reference,
        amount_minor=body.amount_minor,
        now=c.clock.now(),
        event_id=f"dev-{uuid.uuid4().hex[:12]}",
        txn_id=body.txn_id,
        status=body.status,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=request.app), base_url="http://dev"
    ) as client:
        r = await client.post("/v1/webhooks/payments/fake", content=payload, headers=headers)
    return {"webhook_status": r.status_code, "body": r.json()}


@router.get("/outbox")
def dev_outbox(request: Request) -> dict[str, Any]:
    """Messages the recording channels 'sent' (email and WhatsApp are not delivered in local mode)."""
    c = request.app.state.container
    return {
        name: [
            {
                "to": m.to,
                "subject": m.subject,
                "text": m.text,
                "template": m.template,
                "locale": m.locale,
            }
            for m in getattr(ch, "sent", [])
        ]
        for name, ch in c.channels.items()
    }
