import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api, unwrap } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { Badge, Empty, ErrorBox, Loading, Page, placeLabel } from "../components/ui";
import { formatMoney } from "../lib/money";
import { usePropertyQuota } from "./Properties";
import { formatDate } from "../lib/dates";

export default function Home() {
  const { has } = useAuth();
  if (has("platform_admin")) return <Link to="/platform/organizations">Organisations</Link>;
  return has(...STAFF) ? <StaffHome /> : <TenantHome />;
}

/** Whole days from today (local calendar) to an inclusive end date: 0 on the last day. */
export function daysLeft(until: string, today = new Date()): number {
  const [y = 1970, m = 1, d = 1] = until.split("-").map(Number);
  const end = Date.UTC(y, m - 1, d);
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((end - now) / 86_400_000);
}

/** Staff: the access end date an admin set, and a property manager's property limit. */
function MyAccess() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const quota = usePropertyQuota();
  const until = me?.active_until;
  const left = until ? daysLeft(until) : null;
  return (<>
    {until && (
      <div className={`tile${left !== null && left <= 30 ? " tile-warn" : ""}`}>
        <span>{t("dashboard.accessUntil")}</span>
        <strong>{formatDate(until)}</strong>
        <small className="muted">{left === 0 ? t("dashboard.lastDay") : t("dashboard.daysLeft", { count: left ?? 0 })}</small>
      </div>
    )}
    {quota && (
      <div className="tile">
        <span>{t("dashboard.myProperties")}</span>
        <strong>{quota.limit === null ? quota.used : `${quota.used} / ${quota.limit}`}</strong>
        <Link to="/properties">{t("nav.properties")}</Link>
      </div>
    )}
  </>);
}

/** The company's plan set by Anjung (contract v0.12.0): properties used and the end date, for staff. */
function Plan() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const plan = me?.plan;
  if (!plan || (plan.unit_limit == null && !plan.active_until)) return null;
  const left = plan.active_until ? daysLeft(plan.active_until) : null;
  return (<>
    {plan.unit_limit != null && (
      <div className={`tile${(plan.units_used ?? 0) >= plan.unit_limit ? " tile-warn" : ""}`}>
        <span>{t("plan.properties")}</span>
        <strong>{`${plan.units_used ?? 0} / ${plan.unit_limit}`}</strong>
      </div>
    )}
    {plan.active_until && (
      <div className={`tile${left !== null && left <= 30 ? " tile-warn" : ""}`}>
        <span>{t("plan.until")}</span>
        <strong>{formatDate(plan.active_until)}</strong>
        <small className="muted">{left !== null && left < 0 ? t("plan.ended") : left === 0 ? t("dashboard.lastDay") : t("dashboard.daysLeft", { count: left ?? 0 })}</small>
      </div>
    )}
  </>);
}

function StaffHome() {
  const { t } = useTranslation();
  const arrears = useQuery({ queryKey: ["report", "arrears"], queryFn: () => unwrap(api.GET("/v1/reports/arrears", { params: { query: {} } })) });
  const occ = useQuery({ queryKey: ["report", "occupancy"], queryFn: () => unwrap(api.GET("/v1/reports/occupancy")) });
  const totalSpaces = occ.data?.rows.reduce((n, r) => n + r.units_total, 0) ?? 0;
  const occupied = occ.data?.rows.reduce((n, r) => n + r.units_occupied, 0) ?? 0;
  return (
    <Page title={t("dashboard.title")}>
      <ErrorBox error={arrears.error ?? occ.error} />
      <div className="tiles">
        <div className="tile"><span>{t("dashboard.arrears")}</span><strong>{arrears.data ? formatMoney(arrears.data.total_outstanding) : "…"}</strong></div>
        <div className="tile"><span>{t("dashboard.owing")}</span><strong>{arrears.data?.rows.length ?? "…"}</strong></div>
        <div className="tile"><span>{t("dashboard.occupancy")}</span><strong>{occ.data ? `${occupied}/${totalSpaces}` : "…"}</strong></div>
        <Plan />
        <MyAccess />
      </div>
      <p><Link to="/reports">{t("nav.reports")}</Link></p>
    </Page>
  );
}

/** What a tenant needs at a glance: what they owe, what is due next, what is open (plan P4-F3). */
function TenantHome() {
  const { t } = useTranslation();
  const leases = useQuery({ queryKey: ["leases", "mine"], queryFn: () => unwrap(api.GET("/v1/leases", { params: { query: { limit: 10, status: "active" } } })) });
  const invoices = useQuery({ queryKey: ["invoices", "unpaid"], queryFn: () => unwrap(api.GET("/v1/invoices", { params: { query: { limit: 50 } } })) });
  const requests = useQuery({ queryKey: ["maintenance", "mine"], queryFn: () => unwrap(api.GET("/v1/maintenance-requests", { params: { query: { limit: 50 } } })) });
  // a tenancy agreement waiting for this tenant's signature
  const toSign = useQuery({ queryKey: ["leases", "to-sign"], queryFn: () => unwrap(api.GET("/v1/leases", { params: { query: { limit: 10, status: "draft" } } })) });
  const { me } = useAuth();
  const pending = (toSign.data?.items ?? []).filter((l) => l.agreement?.status === "sent" && me?.tenant_id && l.agreement.pending_tenant_ids.includes(me.tenant_id));
  if (leases.isLoading || invoices.isLoading) return <Loading />;
  const owing = (invoices.data?.items ?? []).filter((i) => ["open", "partially_paid", "overdue"].includes(i.status)).sort((a, b) => a.due_date.localeCompare(b.due_date));
  const next = owing[0];
  const open = (requests.data?.items ?? []).filter((r) => !["closed", "cancelled"].includes(r.status));
  const balance = (leases.data?.items ?? []).reduce((n, l) => n + l.balance.amount_minor, 0);
  return (
    <Page title={t("dashboard.title")}>
      <ErrorBox error={leases.error ?? invoices.error ?? requests.error} />
      {pending.map((l) => (
        <p key={l.id} className="notice" role="status">
          {t("agreement.toSign", { place: placeLabel(l) || t("leases.detail") })} <Link className="btn primary sm" to={`/leases/${l.id}`}>{t("agreement.review")}</Link>
        </p>
      ))}
      {(leases.data?.items.length ?? 0) === 0 && pending.length === 0 && <Empty>{t("dashboard.noLease")}</Empty>}
      <div className="tiles">
        <div className="tile"><span>{t("leases.balance")}</span><strong>{formatMoney(balance)}</strong></div>
        <div className="tile"><span>{t("dashboard.nextDue")}</span><strong>{next ? `${formatMoney(next.outstanding)} · ${formatDate(next.due_date)}` : t("dashboard.nothingDue")}</strong>
          {next && <Link className="btn primary" to={`/pay/${next.id}`}>{t("invoices.pay")}</Link>}</div>
        <div className="tile"><span>{t("dashboard.openRequests")}</span><strong>{open.length}</strong><Link to="/maintenance">{t("nav.maintenance")}</Link></div>
      </div>
      {open.slice(0, 3).map((r) => <p key={r.id}><Link to={`/maintenance/${r.id}`}>{r.title}</Link> <Badge value={r.status} /></p>)}
    </Page>
  );
}
