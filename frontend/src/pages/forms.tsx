/** Edit forms and small sections that the detail pages share. Each one sends only what changed. */
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, newIdempotencyKey, unwrap, type Schemas } from "../api/client";
import { Lifecycle } from "../components/Lifecycle";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Select, Table, dateOnly } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";
import { formatMoney, myr, parseMoney } from "../lib/money";

type Property = Schemas["Property"];
type Unit = Schemas["Unit"];
type Tenant = Schemas["Tenant"];
type Lease = Schemas["Lease"];

const submitWith = (run: () => void) => (e: FormEvent) => { e.preventDefault(); run(); };
const centsText = (m: { amount_minor: number }) => (m.amount_minor / 100).toFixed(2);
export const DEPOSIT_TYPES = ["security", "utility", "access_card", "other"] as const;
type DepositType = (typeof DEPOSIT_TYPES)[number];

// ---- property and unit
export function EditProperty({ property, onDone }: { property: Property; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ name: property.name, line1: property.address.line1, city: property.address.city, state: property.address.state, postcode: property.address.postcode });
  const save = useAction(
    () => unwrap(api.PATCH("/v1/properties/{property_id}", { params: { path: { property_id: property.id } }, body: {
      name: f.name, address: { ...property.address, line1: f.line1, city: f.city, state: f.state, postcode: f.postcode },
    } })),
    [["property", property.id], ["properties"]], onDone,
  );
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <form onSubmit={submitWith(() => save.run(undefined as never))} className="card">
      <ErrorBox error={save.error} />
      <Field label={t("properties.name")} error={save.fields["name"]}><Input required value={f.name} onChange={set("name")} /></Field>
      <Field label={t("address.line1")} error={save.fields["address.line1"]}><Input required value={f.line1} onChange={set("line1")} /></Field>
      <Field label={t("address.city")}><Input required value={f.city} onChange={set("city")} /></Field>
      <Field label={t("address.state")}><Input required value={f.state} onChange={set("state")} /></Field>
      <Field label={t("address.postcode")} error={save.fields["address.postcode"]}><Input required pattern="[0-9]{5}" value={f.postcode} onChange={set("postcode")} /></Field>
      <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
    </form>
  );
}

export function EditUnit({ unit, propertyId, wholeUnits = [], onDone }: { unit: Unit; propertyId: string; wholeUnits?: Unit[]; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ label: unit.label, rent: centsText(unit.default_rent), status: unit.status, parent: unit.parent_unit_id ?? "", note: unit.note ?? "" });
  const isBay = unit.type === "parking";
  const rent = parseMoney(f.rent);
  const save = useAction(
    () => unwrap(api.PATCH("/v1/units/{unit_id}", { params: { path: { unit_id: unit.id } }, body: {
      label: f.label, default_rent: myr(rent ?? 0),
      ...(isBay && (f.parent || null) !== (unit.parent_unit_id ?? null) ? { parent_unit_id: f.parent || null } : {}),
      ...(isBay && f.note !== (unit.note ?? "") ? { note: f.note.trim() || null } : {}),
      ...(unit.status !== "occupied" && unit.status !== "inactive" && f.status !== unit.status ? { status: f.status as "vacant" | "maintenance" } : {}),
    } })),
    [["units", propertyId]], onDone,
  );
  const pathId = { params: { path: { unit_id: unit.id } } };
  const refresh = [["units", propertyId], ["property", propertyId]];
  return (
    <>
    <form onSubmit={submitWith(() => rent !== null && save.run(undefined as never))} className="card">
      <ErrorBox error={save.error} />
      <Field label={t("units.label")} error={save.fields["label"]}><Input required maxLength={50} value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
      <Field label={t("units.rent")} error={save.fields["default_rent.amount_minor"] ?? (rent === null ? t("common.invalidAmount") : undefined)}>
        <Input required inputMode="decimal" value={f.rent} onChange={(e) => setF({ ...f, rent: e.target.value })} />
      </Field>
      {isBay && (<>
        <Field label={t("units.allocated")} error={save.fields["parent_unit_id"]}>
          <Select value={f.parent} onChange={(e) => setF({ ...f, parent: e.target.value })}>
            <option value="">{t("units.propertyBay")}</option>
            {wholeUnits.filter((u) => u.status !== "inactive" || u.id === unit.parent_unit_id).map((u) => <option key={u.id} value={u.id}>{t("units.withUnit", { label: u.label })}</option>)}
          </Select>
        </Field>
        <Field label={t("units.note")}><Input maxLength={200} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      </>)}
      {unit.status !== "occupied" && unit.status !== "inactive" && (
        <Field label={t("common.status")}>
          <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as Unit["status"] })}>
            {["vacant", "maintenance"].map((s) => <option key={s} value={s}>{t(`status.${s}`)}</option>)}
          </Select>
        </Field>
      )}
      <Button type="submit" disabled={save.pending || rent === null}>{t("common.save")}</Button>
    </form>
    <Lifecycle name={unit.label} active={unit.status !== "inactive"}
      deactivate={() => unwrap(api.POST("/v1/units/{unit_id}/deactivate", pathId)).then(onDone)}
      reactivate={() => unwrap(api.POST("/v1/units/{unit_id}/reactivate", pathId)).then(onDone)}
      preview={() => unwrap(api.GET("/v1/units/{unit_id}/deletion", pathId))}
      remove={(reason) => unwrap(api.DELETE("/v1/units/{unit_id}", { params: { path: { unit_id: unit.id }, query: { reason } } }))}
      invalidate={refresh} onDeleted={onDone} />
    </>
  );
}

// ---- tenant and consent
export function EditTenant({ tenant, onDone }: { tenant: Tenant; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ name: tenant.full_name, email: tenant.email, phone: tenant.phone ?? "", status: tenant.status });
  const save = useAction(
    () => unwrap(api.PATCH("/v1/tenants/{tenant_id}", { params: { path: { tenant_id: tenant.id } }, body: {
      full_name: f.name,
      ...(f.email !== tenant.email ? { email: f.email } : {}), // the email is locked once the tenant is invited
      phone: f.phone || null,
      ...(f.status !== tenant.status && (f.status === "active" || f.status === "former") ? { status: f.status } : {}),
    } })),
    [["tenant", tenant.id], ["tenants"]], onDone,
  );
  const locked = tenant.status !== "invited" || tenant.linked;
  return (
    <form onSubmit={submitWith(() => save.run(undefined as never))} className="card">
      <ErrorBox error={save.error} />
      <Field label={t("tenants.name")} error={save.fields["full_name"]}><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label={t("tenants.email")} error={save.fields["email"]}>
        <Input required type="email" disabled={locked} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      </Field>
      <Field label={t("tenants.phone")} error={save.fields["phone"]}><Input inputMode="tel" placeholder="+60123456789" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      {(tenant.status === "active" || tenant.status === "former") && (
        <Field label={t("common.status")}>
          <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as Tenant["status"] })}>
            <option value="active">{t("status.active")}</option><option value="former">{t("status.former")}</option>
          </Select>
        </Field>
      )}
      <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
    </form>
  );
}

const PURPOSES = ["lease_administration", "payment_processing", "email_messaging", "whatsapp_messaging", "marketing"] as const;

/** Consent is a history, never an edit: each change is a new record with its purpose, version and time (PDPA). */
export function Consents({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const list = usePaged<Schemas["ConsentRecord"]>(["consents", tenantId], (cursor) =>
    unwrap(api.GET("/v1/tenants/{tenant_id}/consents", { params: { path: { tenant_id: tenantId }, query: { limit: 25, cursor } } })));
  const [f, setF] = useState({ purpose: "email_messaging", version: "2025-11", granted: true });
  const record = useAction(
    () => unwrap(api.POST("/v1/tenants/{tenant_id}/consents", { params: { path: { tenant_id: tenantId } }, body: { purpose: f.purpose as "marketing", policy_version: f.version, granted: f.granted } })),
    [["consents", tenantId]],
  );
  // newest first, so the first record per purpose is the current state
  const current = new Map<string, boolean>();
  for (const c of list.items) if (!current.has(c.purpose)) current.set(c.purpose, c.granted);
  return (
    <section aria-labelledby="consents">
      <h2 id="consents">{t("consent.title")}</h2>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty>{t("consent.none")}</Empty> : (<>
        <ul className="plain">{[...current].map(([p, g]) => <li key={p}>{t(`consent.purposes.${p}`)}: <Badge value={g ? "granted" : "withdrawn"} tone={g ? "good" : "neutral"} /></li>)}</ul>
        <Table rows={list.items} rowKey={(c) => c.purpose + c.recorded_at} columns={[
          { header: t("consent.purpose"), cell: (c) => t(`consent.purposes.${c.purpose}`) },
          { header: t("consent.decision"), cell: (c) => (c.granted ? t("consent.granted") : t("consent.withdrawn")) },
          { header: t("consent.version"), cell: (c) => c.policy_version },
          { header: t("consent.source"), cell: (c) => t(`consent.sources.${c.source}`) },
          { header: t("consent.when"), cell: (c) => dateOnly(c.recorded_at) },
        ]} />
      </>)}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
      <form onSubmit={submitWith(() => record.run(undefined as never))} className="card">
        <ErrorBox error={record.error} />
        <Field label={t("consent.purpose")}>
          <Select value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })}>{PURPOSES.map((p) => <option key={p} value={p}>{t(`consent.purposes.${p}`)}</option>)}</Select>
        </Field>
        <Field label={t("consent.version")} error={record.fields["policy_version"]}><Input required maxLength={50} value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })} /></Field>
        <label className="check"><input type="checkbox" checked={f.granted} onChange={(e) => setF({ ...f, granted: e.target.checked })} />{t("consent.grantedOnBehalf")}</label>
        <Button type="submit" disabled={record.pending}>{t("consent.record")}</Button>
      </form>
    </section>
  );
}

// ---- lease: amend, renew
export function AmendLease({ lease, onDone }: { lease: Lease; onDone: () => void }) {
  const { t } = useTranslation();
  const active = lease.status === "active";
  const draft = lease.status === "draft";
  const [f, setF] = useState({ rent: centsText(lease.monthly_rent), dueDay: String(lease.due_day), notice: String(lease.notice_days), rolling: lease.end_date === null, end: lease.end_date ?? "", effective: "" });
  const rent = parseMoney(f.rent);
  const isBay = lease.unit_type === "parking";
  const [plate, setPlate] = useState(lease.vehicle_plate ?? "");
  // deposits can change only before activation: the deposit invoice is issued when the lease is activated
  const [deposits, setDeposits] = useState<Record<string, string>>(
    () => Object.fromEntries(lease.deposits.map((d) => [d.type, centsText(d.amount)])));
  const depositAmount = (k: DepositType) => (deposits[k]?.trim() ? parseMoney(deposits[k]) : 0);
  const depositsValid = DEPOSIT_TYPES.every((k) => depositAmount(k) !== null);
  const depositList = DEPOSIT_TYPES.flatMap((k) => {
    const v = depositAmount(k);
    const before = lease.deposits.find((d) => d.type === k);
    return v ? [{ type: k, amount: myr(v), ...(before?.description ? { description: before.description } : {}) }] : [];
  });
  const depositsChanged = DEPOSIT_TYPES.some((k) => (depositAmount(k) ?? -1) !== (lease.deposits.find((d) => d.type === k)?.amount.amount_minor ?? 0));
  const save = useAction(
    () => unwrap(api.PATCH("/v1/leases/{lease_id}", { params: { path: { lease_id: lease.id } }, body: {
      monthly_rent: myr(rent ?? 0), due_day: Number(f.dueDay), notice_days: Number(f.notice),
      end_date: f.rolling ? null : f.end, // an explicit null makes the lease rolling
      ...(active && f.effective ? { effective_from: f.effective } : {}),
      ...(draft && depositsChanged ? { deposits: depositList } : {}), // only when changed, so signatures stay
      ...(isBay && plate.trim() !== (lease.vehicle_plate ?? "") ? { vehicle_plate: plate.trim() || null } : {}),
    } })),
    [["lease", lease.id], ["leases"]], onDone,
  );
  return (
    <form onSubmit={submitWith(() => rent !== null && depositsValid && save.run(undefined as never))} className="card">
      <ErrorBox error={save.error} />
      {save.error && (save.error as { code?: string }).code === "lease_overlap" && <p className="notice">{t("leases.overlapHelp")}</p>}
      {active && <p className="notice">{t("leases.amendHelp")}</p>}
      <Field label={t("leases.rent")} error={save.fields["monthly_rent.amount_minor"] ?? (rent === null ? t("common.invalidAmount") : undefined)}><Input required inputMode="decimal" value={f.rent} onChange={(e) => setF({ ...f, rent: e.target.value })} /></Field>
      <Field label={t("leases.dueDay")}><Input type="number" min={1} max={28} value={f.dueDay} onChange={(e) => setF({ ...f, dueDay: e.target.value })} /></Field>
      <Field label={t("leases.noticeDays")}><Input type="number" min={0} max={180} value={f.notice} onChange={(e) => setF({ ...f, notice: e.target.value })} /></Field>
      <label className="check"><input type="checkbox" checked={f.rolling} onChange={(e) => setF({ ...f, rolling: e.target.checked })} />{t("leases.rollingHelp")}</label>
      {!f.rolling && <Field label={t("leases.end")} error={save.fields["end_date"]}><Input required type="date" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></Field>}
      {isBay && <Field label={t("leases.vehiclePlate")} error={save.fields["vehicle_plate"]}><Input maxLength={20} value={plate} onChange={(e) => setPlate(e.target.value)} /></Field>}
      {active && <Field label={t("leases.effectiveFrom")}><Input type="date" value={f.effective} onChange={(e) => setF({ ...f, effective: e.target.value })} /></Field>}
      {draft && (
        <fieldset>
          <legend>{t("leases.deposits")}</legend>
          {DEPOSIT_TYPES.map((k) => (
            <Field key={k} label={t(`leases.depositTypes.${k}`)} error={depositAmount(k) === null ? t("common.invalidAmount") : undefined}>
              <Input inputMode="decimal" placeholder="0.00" value={deposits[k] ?? ""} onChange={(e) => setDeposits({ ...deposits, [k]: e.target.value })} />
            </Field>
          ))}
          {save.fields["deposits"] && <p className="error-text">{save.fields["deposits"]}</p>}
        </fieldset>
      )}
      <Button type="submit" disabled={save.pending || rent === null || !depositsValid}>{t("common.save")}</Button>
    </form>
  );
}

export function RenewLease({ lease, onDone }: { lease: Lease; onDone: (id: string) => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ end: "", rent: "" });
  const rent = f.rent ? parseMoney(f.rent) : null;
  const renew = useAction(
    () => unwrap(api.POST("/v1/leases/{lease_id}/renew", { params: { path: { lease_id: lease.id } }, body: { new_end_date: f.end, ...(rent ? { monthly_rent: myr(rent) } : {}) } })),
    [["leases"]], (l) => onDone(l.id),
  );
  return (
    <form onSubmit={submitWith(() => renew.run(undefined as never))} className="card">
      <ErrorBox error={renew.error} />
      <p className="muted">{t("leases.renewHelp")}</p>
      <Field label={t("leases.newEnd")} error={renew.fields["new_end_date"]}><Input required type="date" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></Field>
      <Field label={t("leases.newRent")} error={f.rent && rent === null ? t("common.invalidAmount") : renew.fields["monthly_rent.amount_minor"]}>
        <Input inputMode="decimal" placeholder={centsText(lease.monthly_rent)} value={f.rent} onChange={(e) => setF({ ...f, rent: e.target.value })} />
      </Field>
      <Button type="submit" disabled={renew.pending || (!!f.rent && rent === null)}>{t("leases.renew")}</Button>
    </form>
  );
}

// ---- lease: ledger, statement, one-off charge
export function LedgerSection({ leaseId }: { leaseId: string }) {
  const { t } = useTranslation();
  const list = usePaged<Schemas["LedgerEntry"]>(["ledger", leaseId], (cursor) =>
    unwrap(api.GET("/v1/leases/{lease_id}/ledger", { params: { path: { lease_id: leaseId }, query: { limit: 50, cursor } } })));
  const [range, setRange] = useState({ from: "", to: "" });
  const [asked, setAsked] = useState(false);
  const stmt = useQuery({
    queryKey: ["statement", leaseId, range.from, range.to], enabled: asked && !!range.from && !!range.to,
    queryFn: () => unwrap(api.GET("/v1/leases/{lease_id}/statement", { params: { path: { lease_id: leaseId }, query: { from: range.from, to: range.to } } })),
  });
  return (
    <section aria-labelledby="ledger">
      <h2 id="ledger">{t("ledger.title")}</h2>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(e) => e.id} columns={[
          { header: t("audit.when"), cell: (e) => dateOnly(e.created_at) },
          { header: t("ledger.type"), cell: (e) => t(`ledger.types.${e.type}`) },
          { header: t("invoices.amount"), cell: (e) => formatMoney(e.amount) },
          { header: t("ledger.balance"), cell: (e) => formatMoney(e.balance_after) },
          { header: t("ledger.memo"), cell: (e) => e.memo ?? "" },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
      <form onSubmit={submitWith(() => setAsked(true))} className="card">
        <h3>{t("ledger.statement")}</h3>
        <Field label={t("ledger.from")}><Input required type="date" value={range.from} onChange={(e) => { setAsked(false); setRange({ ...range, from: e.target.value }); }} /></Field>
        <Field label={t("ledger.to")}><Input required type="date" value={range.to} onChange={(e) => { setAsked(false); setRange({ ...range, to: e.target.value }); }} /></Field>
        <Button type="submit">{t("ledger.show")}</Button>
        <ErrorBox error={stmt.error} />
        {stmt.data && (
          <p role="status">
            {t("ledger.opening")}: <strong>{formatMoney(stmt.data.opening_balance)}</strong> · {t("ledger.closing")}: <strong>{formatMoney(stmt.data.closing_balance)}</strong> · {t("ledger.entries", { count: stmt.data.entries.length })}
          </p>
        )}
      </form>
    </section>
  );
}

const CATEGORIES = ["utility", "repair", "cleaning", "other"] as const;

/** A single bill outside the rent cycle, allowed after move-out too. One key per form: a double click bills once. */
export function ChargeForm({ leaseId, onDone }: { leaseId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ description: "", category: "utility", amount: "", due: "" });
  const [key] = useState(newIdempotencyKey);
  const amount = parseMoney(f.amount);
  const create = useAction(
    () => unwrap(api.POST("/v1/leases/{lease_id}/charges", { params: { path: { lease_id: leaseId }, header: { "Idempotency-Key": key } }, body: {
      description: f.description, category: f.category as "utility", amount: myr(amount ?? 0), due_date: f.due,
    } })),
    [["lease", leaseId], ["ledger", leaseId], ["invoices"]], onDone,
  );
  return (
    <form onSubmit={submitWith(() => amount && create.run(undefined as never))} className="card">
      <ErrorBox error={create.error} />
      <Field label={t("charge.description")} error={create.fields["description"]}><Input required maxLength={200} placeholder={t("charge.example")} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label={t("charge.category")}>
        <Select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c} value={c}>{t(`charge.categories.${c}`)}</option>)}</Select>
      </Field>
      <Field label={t("invoices.amount")} error={create.fields["amount.amount_minor"] ?? (f.amount && amount === null ? t("common.invalidAmount") : undefined)}><Input required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
      <Field label={t("invoices.due")} error={create.fields["due_date"]}><Input required type="date" value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></Field>
      <Button type="submit" disabled={create.pending || !amount}>{t("charge.create")}</Button>
    </form>
  );
}
