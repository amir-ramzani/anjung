import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap, type Schemas } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { useAction } from "../lib/hooks";
import { formatDateTime } from "../lib/dates";
import { Badge, Button, ErrorBox, Field, Input } from "./ui";

type Lease = Schemas["Lease"];
const NOT_SENT: NonNullable<Lease["agreement"]> = { status: "not_sent", signatures: [], pending_tenant_ids: [] };
const TONE = { not_sent: "neutral", sent: "warn", signed: "good" } as const;

/** Whether this organisation makes tenants sign before activation (default on). Staff only can read it. */
export function useAgreementRequired(): boolean {
  const { has } = useAuth();
  const org = useQuery({ queryKey: ["org"], enabled: has(...STAFF), queryFn: () => unwrap(api.GET("/v1/org")) });
  return org.data?.settings.require_signed_agreement !== false;
}

export const agreementOf = (l: Lease) => l.agreement ?? NOT_SENT;

/** The tenancy agreement on a draft lease: staff send it and track signatures; each tenant signs here. */
export function AgreementSection({ lease }: { lease: Lease }) {
  const { t } = useTranslation();
  const { has, me } = useAuth();
  const a = agreementOf(lease);
  const staff = has(...STAFF);
  if (lease.status !== "draft" && a.status === "not_sent") return null; // older leases had no signing step
  const names = new Map((lease.tenants ?? []).map((x) => [x.id, x.full_name]));
  const signedCount = a.signatures.length;
  const total = lease.tenant_ids.length;
  return (
    <section aria-labelledby="agreement" className="agreement">
      <h2 id="agreement">{t("agreement.title")}</h2>
      <p>
        <Badge value={t(`agreement.status.${a.status}`)} tone={TONE[a.status]} />{" "}
        {a.status !== "not_sent" && <span className="muted">{t("agreement.count", { signed: signedCount, total })}</span>}
      </p>
      {a.signatures.length > 0 && (
        <ul className="plain">
          {a.signatures.map((s) => (
            <li key={s.tenant_id}>
              ✓ <strong>{names.get(s.tenant_id) ?? s.full_name}</strong> · {t(`agreement.method.${s.method}`)} · {formatDateTime(s.signed_at)}
            </li>
          ))}
        </ul>
      )}
      {staff ? <StaffAgreement lease={lease} names={names} /> : me?.tenant_id && <TenantSign lease={lease} tenantId={me.tenant_id} />}
    </section>
  );
}

function StaffAgreement({ lease, names }: { lease: Lease; names: Map<string, string> }) {
  const { t } = useTranslation();
  const a = agreementOf(lease);
  const keys = [["lease", lease.id], ["leases"]];
  const send = useAction(() => unwrap(api.POST("/v1/leases/{lease_id}/agreement/send", { params: { path: { lease_id: lease.id } }, body: {} })), keys);
  const paper = useAction((tenantId: string) => unwrap(api.POST("/v1/leases/{lease_id}/agreement/paper", {
    params: { path: { lease_id: lease.id } }, body: { tenant_ids: [tenantId] },
  })), keys);
  if (lease.status !== "draft") return null;
  return (
    <div className="card">
      <ErrorBox error={send.error ?? paper.error} />
      {a.status === "not_sent" && <p className="muted">{t("agreement.sendHelp")}</p>}
      {a.status === "sent" && <p className="muted">{t("agreement.waiting", { names: a.pending_tenant_ids.map((x) => names.get(x) ?? x).join(", ") })}</p>}
      {a.status === "signed" && <p className="success">{t("agreement.readyToActivate")}</p>}
      <div className="actions">
        <Button variant={a.status === "not_sent" ? "primary" : "secondary"} disabled={send.pending}
          onClick={() => { if (a.status === "not_sent" || window.confirm(t("agreement.confirmResend"))) send.run(undefined as never); }}>
          {a.status === "not_sent" ? t("agreement.send") : t("agreement.sendAgain")}
        </Button>
        {a.pending_tenant_ids.map((x) => (
          <Button key={x} variant="secondary" disabled={paper.pending}
            onClick={() => { if (window.confirm(t("agreement.confirmPaper", { name: names.get(x) ?? x }))) paper.run(x); }}>
            {t("agreement.paper", { name: names.get(x) ?? x })}
          </Button>
        ))}
      </div>
    </div>
  );
}

function TenantSign({ lease, tenantId }: { lease: Lease; tenantId: string }) {
  const { t } = useTranslation();
  const a = agreementOf(lease);
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const sign = useAction(() => unwrap(api.POST("/v1/leases/{lease_id}/agreement/sign", {
    params: { path: { lease_id: lease.id } }, body: { full_name: name, agree: true },
  })), [["lease", lease.id], ["leases"]]);
  if (lease.status !== "draft" || a.status === "not_sent") return null;
  const mine = a.signatures.find((s) => s.tenant_id === tenantId);
  if (mine) return <p className="success" role="status">{t("agreement.youSigned", { when: formatDateTime(mine.signed_at) })}</p>;
  const submit = (e: FormEvent) => { e.preventDefault(); if (agree) sign.run(undefined as never); };
  return (
    <form className="card sign" onSubmit={submit}>
      <strong>{t("agreement.pleaseSign")}</strong>
      <p className="muted">{t("agreement.signHelp")}</p>
      <ErrorBox error={sign.error} />
      <Field label={t("agreement.fullName")} error={sign.fields["full_name"]}>
        <Input required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <label className="check"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />{t("agreement.iAgree")}</label>
      <Button type="submit" disabled={!agree || !name.trim() || sign.pending}>{t("agreement.sign")}</Button>
    </form>
  );
}
