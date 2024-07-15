import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from app.container import Container
from app.core.auth import require_roles
from app.core.context import RequestContext, Role
from app.core.errors import NotFound
from app.core.idempotency import run_idempotent
from app.domains.billing.schemas import (
    AllocateRequest,
    ChargeCreate,
    Id,
    Invoice,
    InvoicePage,
    InvoiceRun,
    InvoiceRunCreate,
    InvoiceRunPage,
    InvoiceVoid,
    LedgerEntryPage,
    ManualPaymentCreate,
    Payment,
    PaymentPage,
    Period,
    QrResult,
    Statement,
    WebhookAck,
)
from app.domains.billing.service import (
    BillingService,
    invoice_to_api,
    ledger_to_api,
    payment_to_api,
)
from app.domains.common import CursorQ, IdPath, LimitQ
from app.worker.envelope import make_envelope

router = APIRouter(tags=["Billing"])
_STAFF = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER))
_ADMIN = Depends(require_roles(Role.ORG_ADMIN))
_ANY = Depends(require_roles(Role.ORG_ADMIN, Role.PROPERTY_MANAGER, Role.TENANT))
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _c(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


def _svc(request: Request, ctx: RequestContext) -> BillingService:
    return BillingService(_c(request), ctx)


@router.get("/invoices", response_model=InvoicePage, operation_id="listInvoices")
def list_invoices(
    request: Request,
    status: Annotated[
        Literal["open", "partially_paid", "paid", "overdue", "void"] | None, Query()
    ] = None,
    period: Annotated[Period | None, Query()] = None,
    lease_id: Annotated[Id | None, Query()] = None,
    due_before: Annotated[date | None, Query()] = None,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ANY,
) -> Any:
    items, nxt = _svc(request, ctx).list_invoices(
        limit, cursor, status, period, lease_id, due_before
    )
    return {"items": [invoice_to_api(i) for i in items], "next_cursor": nxt}


@router.get("/invoices/{invoice_id}", response_model=Invoice, operation_id="getInvoice")
def get_invoice(invoice_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    return invoice_to_api(_svc(request, ctx).invoice(invoice_id))


@router.post(
    "/invoices/{invoice_id}/qr",
    response_model=QrResult,
    status_code=201,
    operation_id="createInvoiceQr",
)
def create_qr(invoice_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    qr = _svc(request, ctx).create_qr(invoice_id)
    return {**qr, "amount": {"amount_minor": qr["amount_minor"], "currency": "MYR"}}


@router.post("/invoices/{invoice_id}/void", response_model=Invoice, operation_id="voidInvoice")
def void_invoice(
    invoice_id: IdPath, body: InvoiceVoid, request: Request, ctx: RequestContext = _STAFF
) -> Any:
    return invoice_to_api(_svc(request, ctx).void(invoice_id, body))


@router.post(
    "/invoices/{invoice_id}/payments/manual",
    response_model=Payment,
    status_code=201,
    operation_id="recordManualPayment",
)
def manual_payment(
    invoice_id: IdPath,
    body: ManualPaymentCreate,
    request: Request,
    key: IdemKey,
    ctx: RequestContext = _STAFF,
) -> Any:
    c = _c(request)
    return run_idempotent(
        c,
        ctx,
        key,
        "POST",
        request.url.path,
        body,
        lambda: payment_to_api(_svc(request, ctx).manual_payment(invoice_id, body, key)),
    )


@router.get("/payments/unmatched", response_model=PaymentPage, operation_id="listUnmatchedPayments")
def list_unmatched(
    request: Request, limit: LimitQ = 25, cursor: CursorQ = None, ctx: RequestContext = _ADMIN
) -> Any:
    items, nxt = _svc(request, ctx).unmatched(limit, cursor)
    return {"items": [payment_to_api(i) for i in items], "next_cursor": nxt}


@router.post(
    "/payments/{payment_id}/allocate",
    response_model=Payment,
    operation_id="allocateUnmatchedPayment",
)
def allocate(
    payment_id: IdPath,
    body: AllocateRequest,
    request: Request,
    key: IdemKey,
    ctx: RequestContext = _ADMIN,
) -> Any:
    return run_idempotent(
        _c(request),
        ctx,
        key,
        "POST",
        request.url.path,
        body,
        lambda: payment_to_api(_svc(request, ctx).allocate(payment_id, body)),
    )


@router.get("/invoice-runs", response_model=InvoiceRunPage, operation_id="listInvoiceRuns")
def list_runs(
    request: Request, limit: LimitQ = 25, cursor: CursorQ = None, ctx: RequestContext = _ADMIN
) -> Any:
    items, nxt = _svc(request, ctx).runs(limit, cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/invoice-runs", response_model=InvoiceRun, status_code=202, operation_id="createInvoiceRun"
)
def create_run(
    body: InvoiceRunCreate, request: Request, key: IdemKey, ctx: RequestContext = _ADMIN
) -> Any:
    return run_idempotent(
        _c(request),
        ctx,
        key,
        "POST",
        request.url.path,
        body,
        lambda: _svc(request, ctx).create_run(body),
    )


@router.post(
    "/leases/{lease_id}/charges",
    response_model=Invoice,
    status_code=201,
    operation_id="createLeaseCharge",
)
def create_charge(
    lease_id: IdPath,
    body: ChargeCreate,
    request: Request,
    key: IdemKey,
    ctx: RequestContext = _STAFF,
) -> Any:
    return run_idempotent(
        _c(request),
        ctx,
        key,
        "POST",
        request.url.path,
        body,
        lambda: invoice_to_api(_svc(request, ctx).charge(lease_id, body, key)),
    )


@router.get("/leases/{lease_id}/ledger", response_model=LedgerEntryPage, operation_id="listLedger")
def list_ledger(
    lease_id: IdPath,
    request: Request,
    limit: LimitQ = 25,
    cursor: CursorQ = None,
    ctx: RequestContext = _ANY,
) -> Any:
    items, nxt = _svc(request, ctx).ledger(lease_id, limit, cursor)
    return {"items": [ledger_to_api(i) for i in items], "next_cursor": nxt}


@router.get(
    "/leases/{lease_id}/statement",
    response_model=Statement,
    response_model_by_alias=True,
    operation_id="getStatement",
)
def get_statement(
    lease_id: IdPath,
    request: Request,
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
    ctx: RequestContext = _ANY,
) -> Any:
    return _svc(request, ctx).statement(lease_id, from_, to)


@router.get("/invoices/{invoice_id}/receipt", operation_id="getInvoiceReceipt")
def get_receipt(invoice_id: IdPath, request: Request, ctx: RequestContext = _ANY) -> Any:
    from app.domains.billing.receipts import receipt_download

    svc = _svc(request, ctx)
    svc.invoice(invoice_id)  # access check
    return receipt_download(_c(request), ctx.org_id, invoice_id)


webhooks = APIRouter(tags=["Webhooks"])


@webhooks.post(
    "/webhooks/payments/{provider}", response_model=WebhookAck, operation_id="paymentWebhook"
)
async def payment_webhook(provider: str, request: Request) -> Any:
    """Signature and timestamp are verified on the raw bytes, then the event is queued and acknowledged."""
    c = _c(request)
    prov = c.payments.get(provider)
    if prov is None:
        raise NotFound()
    body = await request.body()  # raw bytes, before any JSON parsing
    event = prov.verify_webhook(
        {k.lower(): v for k, v in request.headers.items()}, body, c.clock.now()
    )
    env = make_envelope(
        "payment_event",
        c.settings.platform_org_id,
        {
            "provider": event.provider,
            "event_id": event.event_id,
            "gateway_txn_id": event.gateway_txn_id,
            "reference": event.reference,
            "amount_minor": event.amount.amount_minor,
            "currency": event.amount.currency.value,
            "status": event.status,
            "paid_at": event.paid_at.isoformat(),
        },
        c.clock.now(),
    )
    c.jobs.enqueue("payment_events", env, group_id=event.reference, dedup_id=event.event_id)
    return JSONResponse({"received": True})


_ = json
