import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { api, newIdempotencyKey, unwrap, type Schemas } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { DocumentsSection } from "../components/Documents";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, Select, Table, dateOnly } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";
import { formatMoney, myr, parseMoney } from "../lib/money";
import { formatDate } from "../lib/dates";

type Invoice = Schemas["Invoice"];
const STATUSES = ["open", "partially_paid", "overdue", "paid", "void"] as const;

export function InvoiceList() {
  const { t } = useTranslation();
  const [status, setStatus] = useState("");
  const list = usePaged<Invoice>(["invoices", status], (cursor) =>
    unwrap(api.GET("/v1/invoices", { params: { query: { limit: 25, cursor, ...(status ? { status: status as Invoice["status"] } : {}) } } })),
  );
  return (
    <Page title={t("invoices.title")} actions={
      <Select aria-label={t("common.status")} value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">{t("common.all")}</option>{STATUSES.map((s) => <option key={s} value={s}>{t(`status.${s}`)}</option>)}
      </Select>}>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(i) => i.id} rowTo={(i) => `/invoices/${i.id}`} columns={[
          { header: t("invoices.number"), cell: (i) => <Link className="row-title" to={`/invoices/${i.id}`}>{i.number}</Link> },
          { header: t("invoices.due"), cell: (i) => formatDate(i.due_date) },
          { header: t("invoices.total"), cell: (i) => formatMoney(i.total) },
          { header: t("invoices.outstanding"), cell: (i) => formatMoney(i.outstanding) },
          { header: t("common.status"), cell: (i) => <Badge value={i.status} /> },
          { header: "", cell: (i) => ["open", "partially_paid", "overdue"].includes(i.status) ? <Link className="btn primary sm" to={`/pay/${i.id}`}>{t("invoices.pay")}</Link> : null },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

export function InvoiceDetail() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { has } = useAuth();
  const q = useQuery({ queryKey: ["invoice", id], queryFn: () => unwrap(api.GET("/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id } } })) });
  const [panel, setPanel] = useState<"pay" | "void" | null>(null);
  if (q.isLoading) return <Loading />;
  const inv = q.data;
  if (!inv) return <ErrorBox error={q.error} />;
  const staff = has(...STAFF);
  const owing = ["open", "partially_paid", "overdue"].includes(inv.status);
  return (
    <Page title={inv.number} actions={<>
      {owing && !staff && <Link className="btn primary" to={`/pay/${inv.id}`}>{t("invoices.pay")}</Link>}
      {staff && owing && <Button onClick={() => setPanel("pay")}>{t("invoices.recordPayment")}</Button>}
      {staff && owing && inv.paid.amount_minor === 0 && <Button variant="danger" onClick={() => setPanel("void")}>{t("invoices.void")}</Button>}
      {inv.paid.amount_minor > 0 && <Receipt id={inv.id} />}
    </>}>
      {panel === "pay" && <ManualPayment invoice={inv} onDone={() => setPanel(null)} />}
      {panel === "void" && <VoidForm invoice={inv} onDone={() => setPanel(null)} />}
      <dl className="facts">
        <dt>{t("common.status")}</dt><dd><Badge value={inv.status} /></dd>
        <dt>{t("invoices.issued")}</dt><dd>{formatDate(inv.issue_date)}</dd>
        <dt>{t("invoices.due")}</dt><dd>{formatDate(inv.due_date)}</dd>
      </dl>
      <Table rows={inv.line_items} rowKey={(l) => l.description + l.amount.amount_minor} columns={[
        { header: t("invoices.description"), cell: (l) => l.description },
        { header: t("invoices.amount"), cell: (l) => formatMoney(l.amount) },
      ]} />
      <p className="totals">
        {t("invoices.total")}: <strong>{formatMoney(inv.total)}</strong> · {t("invoices.paid")}: {formatMoney(inv.paid)} · {t("invoices.outstanding")}: <strong>{formatMoney(inv.outstanding)}</strong>
      </p>
      {/* staff attach receipts or letters; a tenant can add proof of a bank transfer */}
      <DocumentsSection ownerType="invoice" ownerId={inv.id} types={staff ? ["receipt", "other"] : ["other"]} readOnly={inv.status === "void" && !staff} />
    </Page>
  );
}

function Receipt({ id }: { id: string }) {
  const { t } = useTranslation();
  const open = useAction(() => unwrap(api.GET("/v1/invoices/{invoice_id}/receipt", { params: { path: { invoice_id: id } } })), [], (r) => window.open(r.url, "_blank", "noopener"));
  return <Button variant="secondary" onClick={() => open.run(undefined as never)} disabled={open.pending}>{t("invoices.receipt")}</Button>;
}

function ManualPayment({ invoice, onDone }: { invoice: Invoice; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ method: "cash", amount: (invoice.outstanding.amount_minor / 100).toFixed(2), reason: "", reference: "" });
  const [key] = useState(newIdempotencyKey); // one key per form: a double click can never record two payments
  const minor = parseMoney(f.amount);
  const pay = useAction(
    () => unwrap(api.POST("/v1/invoices/{invoice_id}/payments/manual", { params: { path: { invoice_id: invoice.id }, header: { "Idempotency-Key": key } }, body: {
      method: f.method as "cash", amount: myr(minor ?? 0), received_at: new Date().toISOString(), reason: f.reason, ...(f.reference ? { reference: f.reference } : {}),
    } })),
    [["invoice", invoice.id], ["invoices"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); if (minor) pay.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={pay.error} />
      <Field label={t("invoices.method")}>
        <Select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
          {["cash", "bank_transfer", "duitnow_transfer"].map((m) => <option key={m} value={m}>{t(`invoices.methods.${m}`)}</option>)}
        </Select>
      </Field>
      <Field label={t("invoices.amount")} error={pay.fields["amount.amount_minor"] ?? (minor === null ? t("common.invalidAmount") : undefined)}><Input required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
      <Field label={t("invoices.reference")}><Input maxLength={100} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      <Field label={t("invoices.reason")} error={pay.fields["reason"]}><Input required maxLength={500} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      <Button type="submit" disabled={pay.pending || !minor}>{t("common.save")}</Button>
    </form>
  );
}

function VoidForm({ invoice, onDone }: { invoice: Invoice; onDone: () => void }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const v = useAction(() => unwrap(api.POST("/v1/invoices/{invoice_id}/void", { params: { path: { invoice_id: invoice.id } }, body: { reason } })), [["invoice", invoice.id], ["invoices"]], onDone);
  const submit = (e: FormEvent) => { e.preventDefault(); v.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={v.error} />
      <Field label={t("invoices.reason")} error={v.fields["reason"]}><Input required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <Button type="submit" variant="danger" disabled={v.pending}>{t("invoices.void")}</Button>
    </form>
  );
}

export { dateOnly };
