import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, Select, Table, dateOnly } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";

type PrivacyRequest = Schemas["PrivacyRequest"];
const TYPES = ["access", "correction", "deletion", "portability"] as const;

export default function Privacy() {
  const { t } = useTranslation();
  const { has } = useAuth();
  const admin = has("org_admin");
  const list = usePaged<PrivacyRequest>(["privacy-requests"], (cursor) => unwrap(api.GET("/v1/privacy/requests", { params: { query: { limit: 25, cursor } } })));
  const [open, setOpen] = useState(false);
  return (
    <Page title={t("privacy.title")} actions={<>
      {!admin && <DownloadExport />}
      <Button onClick={() => setOpen(!open)}>{t("privacy.newRequest")}</Button>
    </>}>
      {!admin && <p className="muted">{t("privacy.help")}</p>}
      {open && <RequestForm admin={admin} onDone={() => setOpen(false)} />}
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(r) => r.id} columns={[
          { header: t("privacy.type"), cell: (r) => t(`privacy.types.${r.type}`) },
          { header: t("common.status"), cell: (r) => <Badge value={r.status} /> },
          { header: t("privacy.received"), cell: (r) => dateOnly(r.received_at) },
          { header: t("privacy.dueBy"), cell: (r) => dateOnly(r.due_at) },
          { header: "", cell: (r) => admin && (r.status === "received" || r.status === "in_progress") ? <Decide request={r} /> : null },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

/** The export is generated on request and saved as a JSON file: it is the tenant's own data, not shown on screen. */
function DownloadExport() {
  const { t } = useTranslation();
  const dl = useAction(() => unwrap(api.GET("/v1/privacy/export")), [], (data) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "my-anjung-data.json";
    a.click();
    URL.revokeObjectURL(a.href);
  });
  return (<><Button variant="secondary" disabled={dl.pending} onClick={() => dl.run(undefined as never)}>{t("privacy.download")}</Button>{dl.error != null && <ErrorBox error={dl.error} />}</>);
}

function RequestForm({ admin, onDone }: { admin: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ type: "access", details: "", tenantId: "" });
  const create = useAction(
    () => unwrap(api.POST("/v1/privacy/requests", { body: { type: f.type as PrivacyRequest["type"], ...(f.details ? { details: f.details } : {}), ...(admin ? { tenant_id: f.tenantId } : {}) } })),
    [["privacy-requests"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); create.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={create.error} />
      {admin && <Field label={t("privacy.tenantId")} error={create.fields["tenant_id"]}><Input required value={f.tenantId} onChange={(e) => setF({ ...f, tenantId: e.target.value })} /></Field>}
      <Field label={t("privacy.type")}><Select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>{TYPES.map((x) => <option key={x} value={x}>{t(`privacy.types.${x}`)}</option>)}</Select></Field>
      <Field label={t("privacy.details")}><Input maxLength={2000} value={f.details} onChange={(e) => setF({ ...f, details: e.target.value })} /></Field>
      <Button type="submit" disabled={create.pending}>{t("common.save")}</Button>
    </form>
  );
}

function Decide({ request }: { request: PrivacyRequest }) {
  const { t } = useTranslation();
  const move = useAction((status: "in_progress" | "completed" | "rejected") =>
    unwrap(api.PATCH("/v1/privacy/requests/{privacy_request_id}", { params: { path: { privacy_request_id: request.id } }, body: { status } })), [["privacy-requests"]]);
  return (
    <>
      {request.status === "received" && <Button variant="secondary" onClick={() => move.run("in_progress")}>{t("privacy.start")}</Button>}
      <Button onClick={() => { if (request.type !== "deletion" || window.confirm(t("privacy.confirmDeletion"))) move.run("completed"); }}>{t("privacy.complete")}</Button>
      <Button variant="danger" onClick={() => move.run("rejected")}>{t("privacy.reject")}</Button>
      {move.error != null && <ErrorBox error={move.error} />}
    </>
  );
}
