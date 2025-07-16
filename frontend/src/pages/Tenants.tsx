import { useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { DocumentsSection } from "../components/Documents";
import { Lifecycle } from "../components/Lifecycle";
import { Consents, EditTenant } from "./forms";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, placeLabel, Table } from "../components/ui";
import { config } from "../config";
import { copyText } from "../lib/clipboard";
import { formatDate } from "../lib/dates";
import { useAction, usePaged } from "../lib/hooks";

type Tenant = Schemas["Tenant"];

/** Renting now, about to, used to, or never: separate from whether their login is active. */
function Tenancy({ tenant }: { tenant: Tenant }) {
  const { t } = useTranslation();
  if (!tenant.tenancy) return null;
  const tone = { current: "good", upcoming: "warn", past: "neutral", none: "neutral" } as const;
  return <Badge value={t(`tenants.tenancy.${tenant.tenancy}`)} tone={tone[tenant.tenancy]} />;
}

/** "Pelangi · D-5-8 › Master", one line per current or upcoming lease. */
function Rentals({ tenant }: { tenant: Tenant }) {
  const { t } = useTranslation();
  const rentals = tenant.rentals ?? [];
  if (!rentals.length) return <span className="muted">{t("tenants.notRenting")}</span>;
  return (
    <span className="rentals">
      {rentals.map((r) => (
        <span key={r.lease_id}>{placeLabel(r) || "-"}{r.status === "draft" && <> <Badge value="upcoming" tone="neutral" /></>}</span>
      ))}
    </span>
  );
}

export default function Tenants() {
  const { t } = useTranslation();
  const list = usePaged<Tenant>(["tenants"], (cursor) => unwrap(api.GET("/v1/tenants", { params: { query: { limit: 25, cursor } } })));
  const [open, setOpen] = useState(false);
  return (
    <Page title={t("tenants.title")} actions={<Button onClick={() => setOpen(!open)}>{t("tenants.add")}</Button>}>
      {open && <TenantForm onDone={() => setOpen(false)} />}
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(x) => x.id} rowTo={(x) => `/tenants/${x.id}`} columns={[
          { header: t("tenants.name"), cell: (x) => <Link className="row-title" to={`/tenants/${x.id}`}>{x.full_name}</Link> },
          { header: t("tenants.renting"), cell: (x) => <Rentals tenant={x} /> },
          { header: t("tenants.email"), cell: (x) => x.email },
          { header: t("tenants.tenancyLabel"), cell: (x) => <Tenancy tenant={x} /> },
          { header: t("tenants.account"), cell: (x) => <Badge value={x.status} /> },
          { header: "", cell: (x) => x.status === "invited" ? <Link to={`/tenants/${x.id}#invite`}>{t("tenants.invite")}</Link> : null },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

/** Inviting a tenant (contract v0.8.0): share a one-time set-password link from your own WhatsApp,
 * let the app send it over WhatsApp, or send Cognito's invitation email. */
export function InvitePanel({ tenant }: { tenant: Tenant }) {
  const { t } = useTranslation();
  const [result, setResult] = useState<Schemas["TenantInviteResult"] | null>(null);
  const [copied, setCopied] = useState<boolean | null>(null);
  const invite = useAction(
    (channel: Schemas["TenantInviteRequest"]["channel"]) =>
      unwrap(api.POST("/v1/tenants/{tenant_id}/invite", { params: { path: { tenant_id: tenant.id } }, body: { channel } })),
    [["tenants"], ["tenant", tenant.id]],
    (r) => { setResult(r); setCopied(null); },
  );
  const phone = (tenant.notification_preferences?.whatsapp?.number ?? tenant.phone ?? "").replace(/\D/g, "");
  const { me } = useAuth();
  const brandName = me?.branding?.display_name;
  const message = result?.link
    ? t(brandName ? "invite.shareTextBrand" : "invite.shareText", { name: tenant.full_name, brand: brandName, link: result.link, date: formatDate(result.expires_at) })
    : "";
  const copy = async () => {
    const ok = await copyText(result?.link ?? "");
    setCopied(ok);
    if (!ok) linkRef.current?.select(); // let them press Ctrl+C / long-press instead
  };
  const linkRef = useRef<HTMLInputElement>(null);
  return (
    <section aria-labelledby="invite" className="card">
      <h2 id="invite">{t("invite.title")}</h2>
      <p className="muted">{t(config.whatsappSending ? "invite.helpWithSend" : "invite.help")}</p>
      <ErrorBox error={invite.error} />
      <div className="actions">
        <Button disabled={invite.pending} onClick={() => invite.run("link")}>{t("invite.share")}</Button>
        {config.whatsappSending && <Button variant="secondary" disabled={invite.pending} onClick={() => invite.run("whatsapp")}>{t("invite.sendWhatsapp")}</Button>}
        <Button variant="secondary" disabled={invite.pending} onClick={() => invite.run("email")}>{t("invite.sendEmail")}</Button>
      </div>
      {result?.channel === "link" && result.link && (
        <div className="share" role="status">
          <Field label={t("invite.link")}><Input ref={linkRef} readOnly value={result.link} onFocus={(e) => e.target.select()} /></Field>
          <div className="actions">
            <a className="btn primary" href={`https://wa.me/${phone}?text=${encodeURIComponent(message)}`} target="_blank" rel="noreferrer">{t("invite.openWhatsapp")}</a>
            <Button variant="secondary" onClick={() => void copy()}>{copied ? t("invite.copied") : t("invite.copy")}</Button>
          </div>
          {copied === false && <small className="error-text" role="alert">{t("invite.copyFailed")}</small>}
          <small className="muted">{t("invite.linkWarning", { date: formatDate(result.expires_at) })}</small>
        </div>
      )}
      {result?.channel === "whatsapp" && (config.authMode === "mock"
        ? <p className="notice" role="status">{t("invite.demoWhatsapp")}</p> // demo: the WhatsApp channel only records messages
        : <p className="success" role="status">{t("invite.sentWhatsapp", { phone: tenant.phone ?? "" })}</p>)}
      {result?.channel === "email" && <p className="success" role="status">{t("invite.sentEmail", { email: tenant.email })}</p>}
    </section>
  );
}

function TenantForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ full_name: "", email: "", phone: "" });
  const create = useAction(
    () => unwrap(api.POST("/v1/tenants", { body: { full_name: f.full_name, email: f.email, ...(f.phone ? { phone: f.phone } : {}) } })),
    [["tenants"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); create.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={create.error} />
      <Field label={t("tenants.name")} error={create.fields["full_name"]}><Input required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label={t("tenants.email")} error={create.fields["email"]}><Input required type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
      <Field label={t("tenants.phone")} error={create.fields["phone"]}><Input inputMode="tel" placeholder="+60123456789" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      <Button type="submit" disabled={create.pending}>{t("common.save")}</Button>
    </form>
  );
}

export function TenantDetail() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["tenant", id], queryFn: () => unwrap(api.GET("/v1/tenants/{tenant_id}", { params: { path: { tenant_id: id } } })) });
  const [editing, setEditing] = useState(false);
  const nav = useNavigate();
  const pathId = { params: { path: { tenant_id: id } } };
  if (q.isLoading) return <Loading />;
  const x = q.data;
  if (!x) return <ErrorBox error={q.error} />;
  return (
    <Page title={x.full_name} actions={<>
      {x.status !== "anonymised" && <Button variant="secondary" onClick={() => setEditing(!editing)}>{t("common.edit")}</Button>}
    </>}>
      {editing && <EditTenant tenant={x} onDone={() => setEditing(false)} />}
      {x.status === "invited" && !x.linked && <InvitePanel tenant={x} />}
      <dl className="facts">
        <dt>{t("tenants.renting")}</dt>
        <dd>{(x.rentals ?? []).length ? <span className="rentals">{x.rentals!.map((r) => (
          <span key={r.lease_id}><Link to={`/leases/${r.lease_id}`}>{placeLabel(r) || "-"}</Link>{r.status === "draft" && <> <Badge value="upcoming" tone="neutral" /></>}</span>
        ))}</span> : <span className="muted">{t("tenants.notRenting")}</span>}</dd>
        <dt>{t("tenants.email")}</dt><dd>{x.email}</dd>
        <dt>{t("tenants.phone")}</dt><dd>{x.phone ?? "-"}</dd>
        <dt>{t("tenants.tenancyLabel")}</dt><dd><Tenancy tenant={x} /></dd>
        <dt>{t("tenants.account")}</dt><dd><Badge value={x.status} />{x.tenancy === "past" && x.status === "active" && <small className="muted"> {t("tenants.pastHelp")}</small>}</dd>
        {x.identity_document && (<><dt>{t("tenants.identity")}</dt><dd>{x.identity_document.masked_number}</dd></>)}
      </dl>
      {x.status !== "anonymised" && <Consents tenantId={x.id} />}
      {x.status !== "anonymised" && <DocumentsSection ownerType="tenant" ownerId={x.id} types={["identity", "lease_agreement", "other"]} />}
      {x.status !== "anonymised" && (
        <Lifecycle name={x.full_name}
          preview={() => unwrap(api.GET("/v1/tenants/{tenant_id}/deletion", pathId))}
          remove={(reason) => unwrap(api.DELETE("/v1/tenants/{tenant_id}", { params: { path: { tenant_id: id }, query: { reason } } }))}
          invalidate={[["tenant", id], ["tenants"]]}
          onDeleted={(r) => { if (r.outcome === "removed") nav("/tenants"); }} />
      )}
    </Page>
  );
}
