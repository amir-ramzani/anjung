import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, newIdempotencyKey, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { BrandingCard, BrandingFields, brandBody, brandForm } from "./Branding";
import { AppearanceCard } from "../components/AppearanceCard";
import { BarChart } from "../components/Chart";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, Select, Table, dateOnly } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";
import { bpToPercent, formatMoney, myr, parseMoney, percentToBp } from "../lib/money";
import { formatDate, formatDateTime, formatMonth } from "../lib/dates";
import { filenameFrom, saveBlob } from "../lib/download";

// ---- reports
/** The contract requires from and to on the income report: default to the last six months, this one included. */
export function lastSixMonths(now = new Date()): { from: string; to: string } {
  const period = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  return { from: period(new Date(now.getFullYear(), now.getMonth() - 5, 1)), to: period(now) };
}

type ReportName = "arrears" | "occupancy" | "income" | "maintenance";
type ReportQuery = { property_id?: string; from?: string; to?: string };

/** The same report as a CSV file (contract v0.15.0), fetched with the user's token, then saved. */
function CsvButton({ report, query, disabled }: { report: ReportName; query: ReportQuery; disabled?: boolean }) {
  const { t } = useTranslation();
  const get = useAction(async () => {
    const q = { ...query, format: "csv" as const };
    const r = await api.GET(`/v1/reports/${report}` as "/v1/reports/arrears", { params: { query: q }, parseAs: "blob" });
    const blob = await unwrap(Promise.resolve(r)) as unknown as Blob;
    saveBlob(blob, filenameFrom(r.response.headers.get("content-disposition"), `anjung-${report}.csv`));
  }, []);
  return (
    <span className="actions">
      <Button variant="secondary" className="sm" disabled={disabled || get.pending} onClick={() => get.run(undefined as never)}>{t("reports.csv")}</Button>
      {get.error ? <small role="alert" className="error-text">{t("reports.csvFailed")}</small> : null}
    </span>
  );
}

function ReportHead({ title, children }: { title: string; children: ReactNode }) {
  return <div className="page-head report-head"><h2>{title}</h2>{children}</div>;
}

export function Reports() {
  const { t } = useTranslation();
  const def = lastSixMonths();
  const [f, setF] = useState({ property: "", from: def.from, to: def.to });
  const props = useQuery({ queryKey: ["properties", "all"], queryFn: () => unwrap(api.GET("/v1/properties", { params: { query: { limit: 100 } } })) });
  const prop = f.property || undefined;
  const arrears = useQuery({ queryKey: ["report", "arrears", prop], queryFn: () => unwrap(api.GET("/v1/reports/arrears", { params: { query: { property_id: prop } } })) });
  const occ = useQuery({ queryKey: ["report", "occupancy"], queryFn: () => unwrap(api.GET("/v1/reports/occupancy")) });
  const complete = !!f.from && !!f.to; // a month field is empty while someone is still typing
  const validRange = complete && f.from <= f.to;
  const income = useQuery({
    queryKey: ["report", "income", f.from, f.to, prop], enabled: validRange,
    queryFn: () => unwrap(api.GET("/v1/reports/income", { params: { query: { from: f.from, to: f.to, property_id: prop } } })),
  });
  const maint = useQuery({
    queryKey: ["report", "maintenance", f.from, f.to, prop], enabled: validRange,
    queryFn: () => unwrap(api.GET("/v1/reports/maintenance", { params: { query: { from: f.from, to: f.to, property_id: prop } } })),
  });
  const propName = (id: string) => props.data?.items.find((p) => p.id === id)?.name ?? id.slice(-6);
  const buckets = ["current", "1-30", "31-60", "61-90", "90+"];
  const byBucket = buckets.map((b) => (arrears.data?.rows ?? []).filter((r) => r.bucket === b).reduce((n, r) => n + r.outstanding.amount_minor, 0));
  return (
    <Page title={t("reports.title")}>
      <form className="card filters" onSubmit={(e) => e.preventDefault()}>
        <Field label={t("reports.property")}>
          <Select value={f.property} onChange={(e) => setF({ ...f, property: e.target.value })}>
            <option value="">{t("common.all")}</option>{props.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label={t("ledger.from")}><Input type="month" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
        <Field label={t("ledger.to")} error={complete && !validRange ? t("reports.badRange") : undefined}><Input type="month" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
      </form>
      <ErrorBox error={arrears.error ?? occ.error ?? income.error ?? maint.error} />
      <ReportHead title={t("reports.arrears")}><CsvButton report="arrears" query={{ property_id: prop }} /></ReportHead>
      {arrears.isLoading ? <Loading /> : arrears.data!.rows.length === 0 ? <Empty /> : (<>
        <BarChart title={t("reports.arrearsByAge")} labels={buckets} format={(n) => formatMoney(n)} series={[{ name: t("reports.outstanding"), color: "var(--c-warn)", values: byBucket }]} />
        <Table rows={arrears.data!.rows} rowKey={(r) => r.lease_id} columns={[
          { header: t("reports.lease"), cell: (r) => r.lease_id.slice(-6) },
          { header: t("reports.outstanding"), cell: (r) => formatMoney(r.outstanding) },
          { header: t("reports.oldestDue"), cell: (r) => formatDate(r.oldest_due_date) },
          { header: t("reports.daysOverdue"), cell: (r) => r.days_overdue },
          { header: t("reports.bucket"), cell: (r) => r.bucket },
        ]} />
        <p className="totals">{t("reports.total")}: <strong>{formatMoney(arrears.data!.total_outstanding)}</strong></p>
      </>)}
      <ReportHead title={t("reports.occupancy")}><CsvButton report="occupancy" query={{}} /></ReportHead>
      {occ.isLoading ? <Loading /> : occ.data!.rows.length === 0 ? <Empty /> : (
        <Table rows={occ.data!.rows.filter((r) => !prop || r.property_id === prop)} rowKey={(r) => r.property_id} columns={[
          { header: t("reports.property"), cell: (r) => propName(r.property_id) },
          { header: t("reports.spaces"), cell: (r) => r.units_total },
          { header: t("reports.occupied"), cell: (r) => r.units_occupied },
          { header: t("reports.vacant"), cell: (r) => r.units_vacant },
          { header: t("reports.rate"), cell: (r) => `${Math.round(r.occupancy_rate * 100)}%` },
          { header: t("reports.bays"), cell: (r) => r.bays_total ? `${r.bays_occupied ?? 0} / ${r.bays_total}` : "-" },
        ]} />
      )}
      <ReportHead title={t("reports.income")}><CsvButton report="income" query={{ from: f.from, to: f.to, property_id: prop }} disabled={!validRange} /></ReportHead>
      {!validRange ? null : income.isLoading ? <Loading /> : income.data!.rows.length === 0 ? <Empty /> : (<>
        <BarChart title={t("reports.income")} labels={income.data!.rows.map((r) => formatMonth(r.period))} format={(n) => formatMoney(n)} series={[
          { name: t("reports.invoiced"), color: "var(--c-muted)", values: income.data!.rows.map((r) => r.invoiced.amount_minor) },
          { name: t("reports.collected"), color: "var(--teal)", values: income.data!.rows.map((r) => r.collected.amount_minor) },
        ]} />
        <Table rows={income.data!.rows} rowKey={(r) => r.period} columns={[
          { header: t("reports.period"), cell: (r) => formatMonth(r.period) },
          { header: t("reports.invoiced"), cell: (r) => formatMoney(r.invoiced) },
          { header: t("reports.collected"), cell: (r) => formatMoney(r.collected) },
          { header: t("reports.outstanding"), cell: (r) => formatMoney(r.outstanding) },
        ]} />
      </>)}
      <ReportHead title={t("reports.maintenance")}><CsvButton report="maintenance" query={{ from: f.from, to: f.to, property_id: prop }} disabled={!validRange} /></ReportHead>
      <p className="muted">{t("reports.maintenanceHelp")}</p>
      {!validRange ? null : maint.isLoading ? <Loading /> : !maint.data ? null : maint.data.rows.length === 0 ? <Empty /> : (
        <Table rows={maint.data.rows} rowKey={(r) => r.property_id} columns={[
          { header: t("reports.property"), cell: (r) => propName(r.property_id) },
          { header: t("reports.open"), cell: (r) => r.open },
          { header: t("reports.urgent"), cell: (r) => r.open_urgent },
          { header: t("reports.oldestOpen"), cell: (r) => r.oldest_open_days == null ? "-" : t("reports.days", { count: r.oldest_open_days }) },
          { header: t("reports.resolved"), cell: (r) => r.resolved },
          { header: t("reports.avgDays"), cell: (r) => r.avg_days_to_resolve == null ? "-" : t("reports.days", { count: r.avg_days_to_resolve }) },
          { header: t("reports.cancelled"), cell: (r) => r.cancelled },
        ]} />
      )}
    </Page>
  );
}

// ---- staff users: invite, roles, and access (deactivate, or limit to dates)
type User = Schemas["User"];
type Span = "none" | "1m" | "3m" | "1y" | "custom";

/** Local calendar date as YYYY-MM-DD (the app is used in Malaysia, where business dates are local). */
export const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
/** Last day of a period of `months` starting on `from`: 2024-11-21 + 1 year -> 2025-11-20. Month ends clamp. */
export function periodEnd(from: string, months: number): string {
  const [y = 1970, m = 1, d = 1] = from.split("-").map(Number);
  const target = new Date(Date.UTC(y, m - 1 + months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(d, lastDay));
  target.setUTCDate(target.getUTCDate() - 1);
  return target.toISOString().slice(0, 10);
}
const SPANS: Record<Exclude<Span, "none" | "custom">, number> = { "1m": 1, "3m": 3, "1y": 12 };

function AccessBadge({ user }: { user: User }) {
  const { t } = useTranslation();
  const tone = { active: "good", invited: "neutral", scheduled: "warn", expired: "bad", disabled: "bad" } as const;
  return <Badge value={t(`users.access.${user.access}`)} tone={tone[user.access]} />;
}

function Period({ user: u }: { user: User }) {
  const { t } = useTranslation();
  if (!u.active_from && !u.active_until) return <span className="muted">{t("users.noEnd")}</span>;
  if (!u.active_until) return <>{t("users.from", { date: formatDate(u.active_from) })}</>;
  if (!u.active_from) return <>{t("users.until", { date: formatDate(u.active_until) })}</>;
  return <>{`${formatDate(u.active_from)} – ${formatDate(u.active_until)}`}</>;
}

/** Set the dates: quick periods from today (or the chosen start), or any range. */
function AccessDates({ user, onDone }: { user: User; onDone: () => void }) {
  const { t } = useTranslation();
  const [from, setFrom] = useState(user.active_from ?? "");
  const [until, setUntil] = useState(user.active_until ?? "");
  const manager = user.roles.includes("property_manager") && !user.roles.includes("org_admin");
  const [limit, setLimit] = useState(user.unit_quota == null ? "" : String(user.unit_quota));
  const save = useAction(() => unwrap(api.PATCH("/v1/users/{user_id}", { params: { path: { user_id: user.user_id } },
    body: { active_from: from || null, active_until: until || null, ...(manager ? { unit_quota: limit === "" ? null : Number(limit) } : {}) } })), [["users"]], onDone);
  const quick = (months: number) => setUntil(periodEnd(from && from > todayIso() ? from : todayIso(), months));
  return (
    <form className="card" onSubmit={(e) => { e.preventDefault(); save.run(undefined as never); }}>
      <strong>{t("users.settingsFor", { who: user.name || user.email })}</strong>
      <ErrorBox error={save.error} />
      <div className="actions">
        <Button type="button" variant="secondary" onClick={() => quick(1)}>{t("users.span.1m")}</Button>
        <Button type="button" variant="secondary" onClick={() => quick(3)}>{t("users.span.3m")}</Button>
        <Button type="button" variant="secondary" onClick={() => quick(12)}>{t("users.span.1y")}</Button>
        <Button type="button" variant="secondary" onClick={() => setUntil("")}>{t("users.span.none")}</Button>
      </div>
      <Field label={t("users.activeFrom")} error={save.fields["active_from"]}><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
      <Field label={t("users.activeUntil")} error={save.fields["active_until"]}><Input type="date" value={until} min={from || undefined} onChange={(e) => setUntil(e.target.value)} /></Field>
      <p className="muted">{until ? t("users.untilHelp", { date: formatDate(until) }) : t("users.noEndHelp")}</p>
      {manager && (
        <Field label={t("properties.propertyLimit")} error={save.fields["unit_quota"]}>
          <Input type="number" min={0} max={10000} inputMode="numeric" placeholder={t("users.noLimit")} value={limit} onChange={(e) => setLimit(e.target.value)} />
          <small className="muted">{t("users.limitHelp", { used: user.units_managed })}</small>
        </Field>
      )}
      <div className="actions">
        <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
        <Button type="button" variant="secondary" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

export function Users() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const list = usePaged<User>(["users"], (cursor) => unwrap(api.GET("/v1/users", { params: { query: { limit: 50, cursor } } })));
  const blank = { email: "", name: "", role: "property_manager", span: "none" as Span, from: "", until: "", limit: "" };
  const [f, setF] = useState(blank);
  const window_ = (): { active_from?: string; active_until?: string } => {
    if (f.span === "none") return {};
    if (f.span === "custom") return { ...(f.from ? { active_from: f.from } : {}), ...(f.until ? { active_until: f.until } : {}) };
    return { active_until: periodEnd(todayIso(), SPANS[f.span]) };
  };
  const invite = useAction(() => unwrap(api.POST("/v1/users/invite", { body: {
    email: f.email, ...(f.name ? { name: f.name } : {}), roles: [f.role as "org_admin"], ...window_(),
    ...(f.role === "property_manager" && f.limit !== "" ? { unit_quota: Number(f.limit) } : {}),
  } })), [["users"]], () => setF(blank));
  const remove = useAction((id: string) => unwrap(api.DELETE("/v1/users/{user_id}", { params: { path: { user_id: id } } })) as Promise<unknown>, [["users"]]);
  const setStatus = useAction(({ id, status }: { id: string; status: "active" | "disabled" }) =>
    unwrap(api.PATCH("/v1/users/{user_id}", { params: { path: { user_id: id } }, body: { status } })), [["users"]]);
  const [resent, setResent] = useState<string | null>(null);
  // a new welcome email with a fresh set-password link, for staff who have not signed in yet (contract v0.15.0)
  const resend = useAction((u: User) => unwrap(api.POST("/v1/users/{user_id}/invite", { params: { path: { user_id: u.user_id } } })),
    [["users"]], (u) => setResent(u.email));
  const [editing, setEditing] = useState<User | null>(null);
  const submit = (e: FormEvent) => { e.preventDefault(); setResent(null); invite.run(undefined as never); };
  return (
    <Page title={t("users.title")}>
      <form onSubmit={submit} className="card">
        <ErrorBox error={invite.error} />
        <Field label={t("tenants.email")} error={invite.fields["email"]}><Input required type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label={t("tenants.name")}><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={t("users.role")}>
          <Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="property_manager">{t("users.roles.property_manager")}</option><option value="org_admin">{t("users.roles.org_admin")}</option></Select>
        </Field>
        {f.role === "property_manager" && (
          <Field label={t("properties.propertyLimit")} error={invite.fields["unit_quota"]}>
            <Input type="number" min={0} max={10000} inputMode="numeric" placeholder={t("users.noLimit")} value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} />
          </Field>
        )}
        <Field label={t("users.accessLabel")} error={invite.fields["active_until"]}>
          <Select value={f.span} onChange={(e) => setF({ ...f, span: e.target.value as Span })}>
            {(["none", "1m", "3m", "1y", "custom"] as const).map((k) => <option key={k} value={k}>{t(`users.span.${k}`)}</option>)}
          </Select>
        </Field>
        {f.span === "custom" && (<>
          <Field label={t("users.activeFrom")}><Input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
          <Field label={t("users.activeUntil")}><Input type="date" value={f.until} min={f.from || undefined} onChange={(e) => setF({ ...f, until: e.target.value })} /></Field>
        </>)}
        {f.span !== "none" && f.span !== "custom" && <p className="muted">{t("users.untilHelp", { date: formatDate(periodEnd(todayIso(), SPANS[f.span])) })}</p>}
        <p className="muted">{t("users.inviteHelp")}</p>
        <Button type="submit" disabled={invite.pending}>{t("users.invite")}</Button>
      </form>
      {editing && <AccessDates key={editing.user_id} user={editing} onDone={() => setEditing(null)} />}
      <ErrorBox error={list.error ?? remove.error ?? setStatus.error ?? resend.error} />
      {resent && !resend.error && <p className="success" role="status">{t("users.resent", { email: resent })}</p>}
      {list.isLoading ? <Loading /> : (
        <Table rows={list.items} rowKey={(u) => u.user_id} columns={[
          { header: t("tenants.email"), cell: (u) => <span className="rentals"><span>{u.email}</span>{u.name && <span className="muted">{u.name}</span>}</span> },
          { header: t("users.role"), cell: (u) => u.roles.map((r) => t(`users.roles.${r}`, { defaultValue: r })).join(", ") },
          { header: t("users.accessLabel"), cell: (u) => <AccessBadge user={u} /> },
          { header: t("users.period"), cell: (u) => <Period user={u} /> },
          { header: t("nav.properties"), cell: (u) => u.unit_quota == null ? String(u.units_managed) : `${u.units_managed} / ${u.unit_quota}` },
          { header: "", cell: (u) => u.user_id === me?.user_id ? <span className="muted">{t("users.you")}</span> : (
            <span className="actions">
              <Button variant="secondary" className="sm" onClick={() => setEditing(u)}>{t("common.edit")}</Button>
              {u.status === "invited" && <Button variant="secondary" className="sm" disabled={resend.pending} onClick={() => { setResent(null); resend.run(u); }}>{t("users.resend")}</Button>}
              {u.status === "disabled"
                ? <Button variant="secondary" className="sm" disabled={setStatus.pending} onClick={() => setStatus.run({ id: u.user_id, status: "active" })}>{t("users.reactivate")}</Button>
                : <Button variant="danger" className="sm" disabled={setStatus.pending} onClick={() => { if (window.confirm(t("users.confirmDeactivate"))) setStatus.run({ id: u.user_id, status: "disabled" }); }}>{t("users.deactivate")}</Button>}
              <Button variant="danger" className="sm" onClick={() => { if (window.confirm(t("users.confirmRemove"))) remove.run(u.user_id); }}>{t("common.remove")}</Button>
            </span>
          ) },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

/** A company's plan (contract v0.12.0): set by the platform admin, with a reason for the audit log. */
function PlanForm({ org, onDone }: { org: Schemas["Organization"]; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState<{ limit: string; until: string; reason: string }>({
    limit: org.plan?.unit_limit != null ? String(org.plan.unit_limit) : "", until: org.plan?.active_until ?? "", reason: "",
  });
  const save = useAction(() => unwrap(api.PATCH("/v1/platform/organizations/{org_id}/plan", { params: { path: { org_id: org.id } }, body: {
    unit_limit: f.limit ? Number(f.limit) : null, active_until: f.until || null, reason: f.reason,
  } })), [["orgs"]], onDone);
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.run(undefined as never); }} className="card" aria-label={t("plan.editFor", { name: org.name })}>
      <h2 style={{ margin: 0 }}>{t("plan.editFor", { name: org.name })}</h2>
      <ErrorBox error={save.error} />
      <Field label={t("plan.limitLabel")} error={save.fields["unit_limit"]}><Input type="number" min={1} placeholder={t("plan.noLimit")} value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} /></Field>
      <Field label={t("plan.untilLabel")}><Input type="date" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></Field>
      <small className="muted">{t("plan.help")}</small>
      <Field label={t("invoices.reason")} error={save.fields["reason"]}><Input required maxLength={500} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="actions">
        <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
        <Button type="button" variant="secondary" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

/** A company's name and branding, corrected by the platform admin (contract v0.17.0). The logo stays with its org admin. */
function DetailsForm({ org, onDone }: { org: Schemas["Organization"]; onDone: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState(org.name);
  const [brand, setBrand] = useState(() => brandForm(org.branding));
  const [reason, setReason] = useState("");
  const save = useAction(() => unwrap(api.PATCH("/v1/platform/organizations/{org_id}/details", { params: { path: { org_id: org.id } }, body: {
    ...(name.trim() !== org.name ? { name: name.trim() } : {}),
    // left as it was when the company never had branding and nothing was filled in
    ...(org.branding || brand.handle || brand.enabled ? { branding: brandBody(brand) } : {}),
    reason,
  } })), [["orgs"]], onDone);
  const title = t("platform.detailsFor", { name: org.name });
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.run(undefined as never); }} className="card form-stack" aria-label={title}>
      <h2 style={{ margin: 0 }}>{title}</h2>
      <ErrorBox error={save.error} />
      <Field label={t("settings.name")} error={save.fields["name"]}><Input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <h3 style={{ margin: 0 }}>{t("branding.title")}</h3>
      <BrandingFields f={brand} onChange={setBrand} errors={save.fields} orgName={name} />
      <small className="muted">{t("platform.logoByAdmin")}</small>
      <Field label={t("invoices.reason")} error={save.fields["reason"]}><Input required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <div className="actions">
        <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
        <Button type="button" variant="secondary" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}

// ---- organisation settings: late fees are entered as a percentage and sent as integer basis points
export function Settings() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["org"], queryFn: () => unwrap(api.GET("/v1/org")) });
  const [f, setF] = useState({ name: "", dueDay: "1", lead: "7", feeType: "none", grace: "0", fixed: "", percent: "", qStart: "", qEnd: "", sign: true });
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const o = q.data;
    if (!o) return;
    const lf = o.settings.late_fee as { type?: string; grace_days?: number; fixed_amount?: { amount_minor: number }; percent_bp?: number } | undefined;
    setF({
      name: o.name, dueDay: String(o.settings.default_due_day ?? 1), lead: String(o.settings.invoice_lead_days ?? 7),
      feeType: lf?.type ?? "none", grace: String(lf?.grace_days ?? 0),
      fixed: lf?.fixed_amount ? (lf.fixed_amount.amount_minor / 100).toFixed(2) : "", percent: lf?.percent_bp != null ? bpToPercent(lf.percent_bp) : "",
      qStart: o.settings.quiet_hours?.start ?? "", qEnd: o.settings.quiet_hours?.end ?? "",
      sign: o.settings.require_signed_agreement !== false,
    });
  }, [q.data]);
  const fixed = f.fixed ? parseMoney(f.fixed) : null;
  const bp = f.percent ? percentToBp(f.percent) : null;
  const late_fee =
    f.feeType === "fixed" ? { type: "fixed" as const, grace_days: Number(f.grace), fixed_amount: myr(fixed ?? 0) }
    : f.feeType === "percent_per_month" ? { type: "percent_per_month" as const, grace_days: Number(f.grace), percent_bp: bp ?? 0 }
    : { type: "none" as const, grace_days: Number(f.grace) };
  const save = useAction(() => unwrap(api.PATCH("/v1/org", { body: {
    name: f.name, settings: { default_due_day: Number(f.dueDay), invoice_lead_days: Number(f.lead), late_fee, require_signed_agreement: f.sign,
      ...(f.qStart && f.qEnd ? { quiet_hours: { start: f.qStart, end: f.qEnd } } : {}) } } })), [["org"]], () => setSaved(true));
  if (q.isLoading) return <Loading />;
  const bad = (f.feeType === "fixed" && fixed === null) || (f.feeType === "percent_per_month" && bp === null);
  const submit = (e: FormEvent) => { e.preventDefault(); setSaved(false); if (!bad) save.run(undefined as never); };
  return (
    <Page title={t("settings.title")}>
      <AppearanceCard />
      <form onSubmit={submit} className="card">
        <ErrorBox error={save.error} />
        <Field label={t("settings.name")} error={save.fields["name"]}><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={t("leases.dueDay")}><Input type="number" min={1} max={28} value={f.dueDay} onChange={(e) => setF({ ...f, dueDay: e.target.value })} /></Field>
        <Field label={t("settings.leadDays")}><Input type="number" min={0} max={30} value={f.lead} onChange={(e) => setF({ ...f, lead: e.target.value })} /></Field>
        <Field label={t("settings.lateFee")}>
          <Select value={f.feeType} onChange={(e) => setF({ ...f, feeType: e.target.value })}>
            <option value="none">{t("settings.fee.none")}</option><option value="fixed">{t("settings.fee.fixed")}</option><option value="percent_per_month">{t("settings.fee.percent")}</option>
          </Select>
        </Field>
        {f.feeType !== "none" && <Field label={t("settings.graceDays")}><Input type="number" min={0} max={60} value={f.grace} onChange={(e) => setF({ ...f, grace: e.target.value })} /></Field>}
        {f.feeType === "fixed" && <Field label={t("settings.feeAmount")} error={f.fixed && fixed === null ? t("common.invalidAmount") : undefined}><Input inputMode="decimal" value={f.fixed} onChange={(e) => setF({ ...f, fixed: e.target.value })} /></Field>}
        {f.feeType === "percent_per_month" && <Field label={t("settings.feePercent")} error={f.percent && bp === null ? t("common.invalidPercent") : undefined}><Input inputMode="decimal" placeholder="1.5" value={f.percent} onChange={(e) => setF({ ...f, percent: e.target.value })} /></Field>}
        <Field label={t("settings.quietFrom")}><Input type="time" value={f.qStart} onChange={(e) => setF({ ...f, qStart: e.target.value })} /></Field>
        <Field label={t("settings.quietTo")}><Input type="time" value={f.qEnd} onChange={(e) => setF({ ...f, qEnd: e.target.value })} /></Field>
        <label className="check"><input type="checkbox" checked={f.sign} onChange={(e) => setF({ ...f, sign: e.target.checked })} />{t("settings.requireSigned")}</label>
        <Button type="submit" disabled={save.pending || bad}>{t("common.save")}</Button>
        {saved && <p role="status" className="success">{t("common.saved")}</p>}
      </form>
      <BrandingCard />
    </Page>
  );
}

// ---- audit log
export function Audit() {
  const { t } = useTranslation();
  const list = usePaged<Schemas["AuditEntry"]>(["audit"], (cursor) => unwrap(api.GET("/v1/audit-log", { params: { query: { limit: 50, cursor } } })));
  return (
    <Page title={t("audit.title")}>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(a) => a.id} columns={[
          { header: t("audit.when"), cell: (a) => formatDateTime(a.created_at) },
          { header: t("audit.action"), cell: (a) => a.action },
          { header: t("audit.actor"), cell: (a) => a.actor_role ?? a.actor_id.slice(0, 8) },
          { header: t("audit.entity"), cell: (a) => `${a.entity_type} ${a.entity_id.slice(-6)}` },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

// ---- unmatched payments
export function Unmatched() {
  const { t } = useTranslation();
  const list = usePaged<Schemas["Payment"]>(["unmatched"], (cursor) => unwrap(api.GET("/v1/payments/unmatched", { params: { query: { limit: 25, cursor } } })));
  const [target, setTarget] = useState<string | null>(null);
  return (
    <Page title={t("unmatched.title")}>
      <p className="muted">{t("unmatched.help")}</p>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(p) => p.id} columns={[
          { header: t("invoices.amount"), cell: (p) => formatMoney(p.amount) },
          { header: t("unmatched.received"), cell: (p) => dateOnly(p.received_at) },
          { header: t("invoices.reference"), cell: (p) => p.gateway_reference ?? "" },
          { header: "", cell: (p) => <Button variant="secondary" onClick={() => setTarget(p.id)}>{t("unmatched.allocate")}</Button> },
        ]} />
      )}
      {target && <Allocate paymentId={target} onDone={() => setTarget(null)} />}
    </Page>
  );
}

function Allocate({ paymentId, onDone }: { paymentId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ invoice: "", reason: "" });
  const [key] = useState(newIdempotencyKey);
  const go = useAction(
    () => unwrap(api.POST("/v1/payments/{payment_id}/allocate", { params: { path: { payment_id: paymentId }, header: { "Idempotency-Key": key } }, body: { invoice_id: f.invoice, reason: f.reason } })),
    [["unmatched"], ["invoices"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); go.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={go.error} />
      <Field label={t("unmatched.invoiceId")} error={go.fields["invoice_id"]}><Input required value={f.invoice} onChange={(e) => setF({ ...f, invoice: e.target.value })} /></Field>
      <Field label={t("invoices.reason")} error={go.fields["reason"]}><Input required maxLength={500} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      <Button type="submit" disabled={go.pending}>{t("unmatched.allocate")}</Button>
    </form>
  );
}

// ---- platform: organisations
export function Organisations() {
  const { t } = useTranslation();
  const list = usePaged<Schemas["Organization"]>(["orgs"], (cursor) => unwrap(api.GET("/v1/platform/organizations", { params: { query: { limit: 50, cursor } } })));
  const [f, setF] = useState({ name: "", email: "", reason: "", handle: "", regNo: "", limit: "", until: "" });
  const [editing, setEditing] = useState<{ id: string; what: "plan" | "details" } | null>(null);
  const toggle = (id: string, what: "plan" | "details") => setEditing(editing?.id === id && editing.what === what ? null : { id, what });
  const create = useAction(() => unwrap(api.POST("/v1/platform/organizations", { body: {
    name: f.name, initial_admin: { email: f.email }, reason: f.reason,
    // the company's plan (contract v0.12.0): its org admin divides it up between managers
    ...(f.limit || f.until ? { plan: { ...(f.limit ? { unit_limit: Number(f.limit) } : {}), ...(f.until ? { active_until: f.until } : {}) } } : {}),
    // optional branding (contract v0.11.0): the company's admin can finish it under Settings
    ...(f.handle.trim() ? { branding: { enabled: true, handle: f.handle.trim().toLowerCase(), ...(f.regNo.trim() ? { company_reg_no: f.regNo.trim() } : {}) } } : {}),
  } })), [["orgs"]], () => setF({ name: "", email: "", reason: "", handle: "", regNo: "", limit: "", until: "" }));
  const setStatus = useAction((v: { id: string; status: "active" | "suspended" | "closed" }) => {
    const reason = window.prompt(t("platform.reasonPrompt"));
    if (!reason) return Promise.resolve(null);
    return unwrap(api.PATCH("/v1/platform/organizations/{org_id}", { params: { path: { org_id: v.id } }, body: { status: v.status, reason } }));
  }, [["orgs"]]);
  const [sent, setSent] = useState<number | null>(null);
  // a new welcome email for a company's admins who have not signed in yet (contract v0.15.0)
  const resendAdmin = useAction((id: string) => {
    const reason = window.prompt(t("platform.reasonPrompt"));
    if (!reason) return Promise.resolve(null);
    return unwrap(api.POST("/v1/platform/organizations/{org_id}/admin-invite", { params: { path: { org_id: id } }, body: { reason } }));
  }, [], (r) => setSent(r ? r.sent : null));
  const submit = (e: FormEvent) => { e.preventDefault(); create.run(undefined as never); };
  return (
    <Page title={t("platform.title")}>
      <form onSubmit={submit} className="card">
        <ErrorBox error={create.error} />
        <Field label={t("settings.name")} error={create.fields["name"]}><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={t("platform.adminEmail")} error={create.fields["initial_admin.email"]}><Input required type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label={t("branding.handleOptional")} error={create.fields["branding.handle"]}><Input pattern="[a-z0-9][a-z0-9-]{1,28}[a-z0-9]" placeholder="rumah-kita" value={f.handle} onChange={(e) => setF({ ...f, handle: e.target.value })} /></Field>
        <Field label={t("branding.regNo")}><Input maxLength={50} value={f.regNo} onChange={(e) => setF({ ...f, regNo: e.target.value })} /></Field>
        <Field label={t("plan.limitLabel")} error={create.fields["plan.unit_limit"]}><Input type="number" min={1} placeholder={t("plan.noLimit")} value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} /></Field>
        <Field label={t("plan.untilLabel")} error={create.fields["plan.active_until"]}><Input type="date" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></Field>
        <Field label={t("invoices.reason")} error={create.fields["reason"]}><Input required maxLength={500} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
        <Button type="submit" disabled={create.pending}>{t("platform.create")}</Button>
      </form>
      <ErrorBox error={list.error ?? setStatus.error ?? resendAdmin.error} />
      {sent != null && !resendAdmin.error && <p className="success" role="status">{t("platform.adminInviteSent", { count: sent })}</p>}
      {list.isLoading ? <Loading /> : (
        <Table rows={list.items} rowKey={(o) => o.id} columns={[
          { header: t("settings.name"), cell: (o) => o.name },
          { header: t("common.status"), cell: (o) => <Badge value={o.status} /> },
          { header: t("plan.properties"), cell: (o) => o.plan ? (o.plan.unit_limit == null ? `${o.plan.units_used}` : `${o.plan.units_used} / ${o.plan.unit_limit}`) : "-" },
          { header: t("plan.until"), cell: (o) => o.plan?.active_until ? <>{formatDate(o.plan.active_until)} {o.plan.status && o.plan.status !== "active" && <Badge value={o.plan.status} />}</> : "-" },
          { header: "", cell: (o) => o.status !== "closed" ? <Button variant="secondary" onClick={() => toggle(o.id, "details")}>{t("platform.editDetails")}</Button> : null },
          { header: "", cell: (o) => <Button variant="secondary" onClick={() => toggle(o.id, "plan")}>{t("plan.edit")}</Button> },
          { header: "", cell: (o) => o.status === "active" ? <Button variant="secondary" disabled={resendAdmin.pending} onClick={() => { setSent(null); resendAdmin.run(o.id); }}>{t("platform.resendAdmin")}</Button> : null },
          { header: "", cell: (o) => o.status === "active" ? <Button variant="danger" onClick={() => setStatus.run({ id: o.id, status: "suspended" })}>{t("platform.suspend")}</Button>
            : o.status === "suspended" ? <Button variant="secondary" onClick={() => setStatus.run({ id: o.id, status: "active" })}>{t("platform.reactivate")}</Button> : null },
        ]} />
      )}
      {(() => {
        const org = editing && list.items.find((o) => o.id === editing.id);
        if (!org || !editing) return null;
        return editing.what === "plan"
          ? <PlanForm key={org.id} org={org} onDone={() => setEditing(null)} />
          : <DetailsForm key={org.id} org={org} onDone={() => setEditing(null)} />;
      })()}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}
