import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, unwrap, type Schemas } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { AgreementSection, agreementOf, useAgreementRequired } from "../components/Agreement";
import { DocumentsSection, TENANT_DOC_TYPES } from "../components/Documents";
import { Lifecycle } from "../components/Lifecycle";
import { AmendLease, ChargeForm, DEPOSIT_TYPES, LedgerSection, RenewLease } from "./forms";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, placeLabel, Select, Table } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";
import { formatMoney, myr, parseMoney } from "../lib/money";
import { formatDate } from "../lib/dates";

type Lease = Schemas["Lease"];
const tenantNames = (l: Lease) => (l.tenants ?? []).map((x) => x.full_name).join(", ");

export function LeaseList() {
  const { t } = useTranslation();
  const { has } = useAuth();
  const list = usePaged<Lease>(["leases"], (cursor) => unwrap(api.GET("/v1/leases", { params: { query: { limit: 25, cursor } } })));
  return (
    <Page title={t("leases.title")} actions={has(...STAFF) && <Link className="btn primary" to="/leases/new">{t("leases.add")}</Link>}>
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(l) => l.id} rowTo={(l) => `/leases/${l.id}`} columns={[
          { header: t("leases.tenants"), cell: (l) => <Link className="row-title" to={`/leases/${l.id}`}>{tenantNames(l) || formatDate(l.start_date)}</Link> },
          { header: t("leases.unit"), cell: (l) => placeLabel(l) || "-" },
          { header: t("leases.period"), cell: (l) => `${formatDate(l.start_date)} – ${l.end_date ? formatDate(l.end_date) : t("leases.rolling")}` },
          { header: t("leases.rent"), cell: (l) => formatMoney(l.monthly_rent) },
          { header: t("leases.balance"), cell: (l) => formatMoney(l.balance) },
          { header: t("common.status"), cell: (l) => l.status === "draft" && agreementOf(l).status === "sent"
            ? <Badge value={t("agreement.awaiting")} tone="warn" /> : <Badge value={l.status} /> },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

export function LeaseForm() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const props = useQuery({ queryKey: ["properties", "all"], queryFn: () => unwrap(api.GET("/v1/properties", { params: { query: { limit: 100, status: "active" } } })) });
  const tenants = useQuery({ queryKey: ["tenants", "all"], queryFn: () => unwrap(api.GET("/v1/tenants", { params: { query: { limit: 100 } } })) });
  const [propertyId, setPropertyId] = useState("");
  const units = useQuery({
    queryKey: ["units", propertyId, "lettable"], enabled: !!propertyId,
    queryFn: () => unwrap(api.GET("/v1/properties/{property_id}/units", { params: { path: { property_id: propertyId }, query: { limit: 100 } } })),
  });
  const [f, setF] = useState({ unit: "", tenantIds: [] as string[], start: "", end: "", rolling: false, notice: "30", rent: "", dueDay: "1" });
  const [deposits, setDeposits] = useState<Record<string, string>>({});
  const [plate, setPlate] = useState("");
  const rent = parseMoney(f.rent);
  const depositList = DEPOSIT_TYPES.flatMap((k) => {
    const v = deposits[k] ? parseMoney(deposits[k]) : null;
    return v && v > 0 ? [{ type: k, amount: myr(v) }] : [];
  });
  const create = useAction(
    () => unwrap(api.POST("/v1/leases", { body: {
      unit_id: f.unit, tenant_ids: f.tenantIds, start_date: f.start, ...(f.rolling ? {} : { end_date: f.end }),
      notice_days: Number(f.notice), monthly_rent: myr(rent ?? 0), deposits: depositList, due_day: Number(f.dueDay),
      ...(isBay && plate.trim() ? { vehicle_plate: plate.trim() } : {}),
    } })),
    [["leases"]], (l) => nav(`/leases/${l.id}`),
  );
  const all = units.data?.items ?? [];
  // a bay allocated to a unit let as a whole comes with that unit, so it is not offered on its own
  const letWhole = new Set(all.filter((u) => u.type === "whole_unit" && u.letting_mode === "whole").map((u) => u.id));
  const lettable = all.filter((u) => u.status !== "inactive" && (u.type === "room"
    || (u.type === "whole_unit" && u.letting_mode === "whole")
    || (u.type === "parking" && !letWhole.has(u.parent_unit_id ?? ""))));
  const bays = lettable.filter((u) => u.type === "parking");
  const isBay = bays.some((u) => u.id === f.unit);
  const hosts = (units.data?.items ?? []).filter((u) => u.type !== "room" && lettable.some((r) => r.type === "room" && r.parent_unit_id === u.id));
  const submit = (e: FormEvent) => { e.preventDefault(); if (rent !== null) create.run(undefined as never); };
  const toggle = (id: string) => setF({ ...f, tenantIds: f.tenantIds.includes(id) ? f.tenantIds.filter((x) => x !== id) : [...f.tenantIds, id] });
  return (
    <Page title={t("leases.add")}>
      <form onSubmit={submit} className="card">
        <ErrorBox error={create.error} />
        {create.error && (create.error as { code?: string }).code === "lease_overlap" && <p className="notice">{t("leases.overlapHelp")}</p>}
        <Field label={t("leases.property")}>
          <Select required value={propertyId} onChange={(e) => { setPropertyId(e.target.value); setF({ ...f, unit: "" }); }}>
            <option value="" />{props.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        {propertyId && units.isSuccess && lettable.length === 0 && <p className="notice" role="status">{t("leases.noLettable")}</p>}
        <Field label={t("leases.unit")} error={create.fields["unit_id"]}>
          <Select required value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} disabled={!propertyId}>
            <option value="" />
            {lettable.filter((u) => u.type === "whole_unit").map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
            {hosts.map((h) => (
              <optgroup key={h.id} label={`${h.label} · ${t("units.byRoom")}`}>
                {lettable.filter((u) => u.type === "room" && u.parent_unit_id === h.id).map((u) => <option key={u.id} value={u.id}>{h.label} › {u.label}</option>)}
              </optgroup>
            ))}
            {bays.length > 0 && (
              <optgroup label={t("units.bays")}>
                {bays.map((u) => <option key={u.id} value={u.id}>{t("units.bayLabel", { label: u.label })}</option>)}
              </optgroup>
            )}
          </Select>
        </Field>
        {isBay && (
          <Field label={t("leases.vehiclePlate")} error={create.fields["vehicle_plate"]}>
            <Input maxLength={20} placeholder="WXY 1234" value={plate} onChange={(e) => setPlate(e.target.value)} />
          </Field>
        )}
        <fieldset>
          <legend>{t("leases.tenants")}</legend>
          {tenants.data?.items.filter((x) => x.status !== "former" && x.status !== "anonymised").map((x) => (
            <label key={x.id} className="check"><input type="checkbox" checked={f.tenantIds.includes(x.id)} onChange={() => toggle(x.id)} />{x.full_name}</label>
          ))}
          {create.fields["tenant_ids"] && <small role="alert" className="error-text">{create.fields["tenant_ids"]}</small>}
        </fieldset>
        <Field label={t("leases.start")} error={create.fields["start_date"]}><Input required type="date" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></Field>
        <label className="check"><input type="checkbox" checked={f.rolling} onChange={(e) => setF({ ...f, rolling: e.target.checked })} />{t("leases.rollingHelp")}</label>
        {f.rolling ? (
          <Field label={t("leases.noticeDays")}><Input type="number" min={0} max={180} value={f.notice} onChange={(e) => setF({ ...f, notice: e.target.value })} /></Field>
        ) : (
          <Field label={t("leases.end")} error={create.fields["end_date"]}><Input required type="date" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></Field>
        )}
        <Field label={t("leases.rent")} error={create.fields["monthly_rent.amount_minor"] ?? (f.rent && rent === null ? t("common.invalidAmount") : undefined)}>
          <Input required inputMode="decimal" value={f.rent} onChange={(e) => setF({ ...f, rent: e.target.value })} />
        </Field>
        <Field label={t("leases.dueDay")}><Input type="number" min={1} max={28} value={f.dueDay} onChange={(e) => setF({ ...f, dueDay: e.target.value })} /></Field>
        <fieldset>
          <legend>{t("leases.deposits")}</legend>
          {DEPOSIT_TYPES.map((k) => (
            <Field key={k} label={t(`leases.depositTypes.${k}`)}>
              <Input inputMode="decimal" placeholder="0.00" value={deposits[k] ?? ""} onChange={(e) => setDeposits({ ...deposits, [k]: e.target.value })} />
            </Field>
          ))}
        </fieldset>
        <Button type="submit" disabled={create.pending || rent === null || f.tenantIds.length === 0}>{t("common.save")}</Button>
      </form>
    </Page>
  );
}

export function LeaseDetail() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { has } = useAuth();
  const lease = useQuery({ queryKey: ["lease", id], queryFn: () => unwrap(api.GET("/v1/leases/{lease_id}", { params: { path: { lease_id: id } } })) });
  const invoices = usePaged<Schemas["Invoice"]>(["invoices", "lease", id], (cursor) =>
    unwrap(api.GET("/v1/invoices", { params: { query: { lease_id: id, limit: 50, cursor } } })),
    !!lease.data && lease.data.status !== "draft"); // a draft has no invoices yet
  const agreementRequired = useAgreementRequired();
  const activate = useAction(() => unwrap(api.POST("/v1/leases/{lease_id}/activate", { params: { path: { lease_id: id } } })), [["lease", id], ["leases"], ["invoices"]]);
  const [ending, setEnding] = useState(false);
  const [panel, setPanel] = useState<"amend" | "renew" | "charge" | null>(null);
  const nav = useNavigate();
  if (lease.isLoading) return <Loading />;
  const l = lease.data;
  if (!l) return <ErrorBox error={lease.error} />;
  const staff = has(...STAFF);
  return (
    <Page title={t("leases.detail")} actions={staff && (<>
      {l.status === "draft" && <Button onClick={() => activate.run(undefined as never)}
        disabled={activate.pending || (agreementRequired && agreementOf(l).status !== "signed")}
        title={agreementRequired && agreementOf(l).status !== "signed" ? t("agreement.activateHelp") : undefined}>{t("leases.activate")}</Button>}
      {(l.status === "draft" || l.status === "active") && <Button variant="secondary" onClick={() => setPanel(panel === "amend" ? null : "amend")}>{t("leases.amend")}</Button>}
      {l.status === "active" && l.end_date !== null && <Button variant="secondary" onClick={() => setPanel(panel === "renew" ? null : "renew")}>{t("leases.renew")}</Button>}
      {l.status !== "draft" && <Button variant="secondary" onClick={() => setPanel(panel === "charge" ? null : "charge")}>{t("charge.add")}</Button>}
      {l.status === "active" && <Button variant="danger" onClick={() => setEnding(!ending)}>{t("leases.terminate")}</Button>}
    </>)}>
      <ErrorBox error={activate.error} />
      {ending && <TerminateForm lease={l} onDone={() => setEnding(false)} />}
      {panel === "amend" && <AmendLease lease={l} onDone={() => setPanel(null)} />}
      {panel === "renew" && <RenewLease lease={l} onDone={(id) => { setPanel(null); nav(`/leases/${id}`); }} />}
      {panel === "charge" && <ChargeForm leaseId={l.id} onDone={() => setPanel(null)} />}
      <dl className="facts">
        <dt>{t("leases.tenants")}</dt>
        <dd>{(l.tenants ?? []).length ? l.tenants!.map((x, i) => <span key={x.id}>{i > 0 && ", "}{staff ? <Link to={`/tenants/${x.id}`}>{x.full_name}</Link> : x.full_name}</span>) : "-"}</dd>
        <dt>{t("leases.unit")}</dt>
        <dd>{staff ? <Link to={`/properties/${l.property_id}`}>{placeLabel(l) || "-"}</Link> : placeLabel(l) || "-"}</dd>
        {(l.included_bays ?? []).length > 0 && (<><dt>{t("units.bays")}</dt><dd>{t("leases.includedBays", { labels: l.included_bays!.map((b) => b.label).join(", ") })}</dd></>)}
        {l.unit_type === "parking" && (<><dt>{t("leases.vehiclePlate")}</dt><dd>{l.vehicle_plate ?? "-"}</dd></>)}
        <dt>{t("common.status")}</dt><dd><Badge value={l.status} /></dd>
        <dt>{t("leases.start")}</dt><dd>{formatDate(l.start_date)}</dd>
        <dt>{t("leases.end")}</dt><dd>{l.end_date ? formatDate(l.end_date) : `${t("leases.rolling")} (${t("leases.noticeDays")}: ${l.notice_days})`}</dd>
        <dt>{t("leases.rent")}</dt><dd>{formatMoney(l.monthly_rent)}</dd>
        <dt>{t("leases.balance")}</dt><dd>{formatMoney(l.balance)}</dd>
        {l.credit.amount_minor > 0 && (<><dt>{t("leases.credit")}</dt><dd>{formatMoney(l.credit)}</dd></>)}
        {l.status === "draft" ? (<>
          <dt>{t("leases.deposits")}</dt>
          <dd>{l.deposits.length ? l.deposits.map((d) => `${t(`leases.depositTypes.${d.type}`)} ${formatMoney(d.amount)}`).join(", ") : "-"}</dd>
        </>) : (<>
          <dt>{t("leases.depositsHeld")}</dt>
          <dd>{(l.deposits_held ?? []).length ? l.deposits_held!.map((d) => `${t(`leases.depositTypes.${d.type}`)} ${formatMoney(d.amount)}`).join(", ") : "-"}</dd>
        </>)}
      </dl>
      {staff && l.status === "draft" && agreementRequired && agreementOf(l).status !== "signed" && <p className="notice">{t("agreement.activateHelp")}</p>}
      <AgreementSection lease={l} />
      <DocumentsSection ownerType="lease" ownerId={l.id} readOnly={l.status === "draft" && !staff}
        types={staff ? ["lease_agreement", "inspection_report", "identity", "other"] : TENANT_DOC_TYPES.filter((x) => x !== "maintenance_photo")} />
      {l.status !== "draft" && <LedgerSection leaseId={l.id} />}
      <h2>{t("invoices.title")}</h2>
      {invoices.isLoading ? <Loading /> : invoices.items.length === 0 ? <Empty /> : (
        <Table rows={invoices.items} rowKey={(i) => i.id} rowTo={(i) => `/invoices/${i.id}`} columns={[
          { header: t("invoices.number"), cell: (i) => <Link className="row-title" to={`/invoices/${i.id}`}>{i.number}</Link> },
          { header: t("invoices.due"), cell: (i) => formatDate(i.due_date) },
          { header: t("invoices.total"), cell: (i) => formatMoney(i.total) },
          { header: t("common.status"), cell: (i) => <Badge value={i.status} /> },
        ]} />
      )}
      {staff && l.status === "draft" && (
        <Lifecycle name={l.unit_label ?? l.id} deleteLabel={t("lifecycle.deleteDraft")}
          preview={() => unwrap(api.GET("/v1/leases/{lease_id}/deletion", { params: { path: { lease_id: id } } }))}
          remove={(reason) => unwrap(api.DELETE("/v1/leases/{lease_id}", { params: { path: { lease_id: id }, query: { reason } } }))}
          invalidate={[["leases"], ["lease", id]]} onDeleted={() => nav("/leases")} />
      )}
    </Page>
  );
}

function TerminateForm({ lease, onDone }: { lease: Lease; onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ date: "", reason: "", waive: false });
  const held = lease.deposits_held ?? [];
  const [kept, setKept] = useState<Record<string, string>>({});
  const deduction = held.flatMap((d) => { const v = kept[d.type] ? parseMoney(kept[d.type]!) : null; return v ? [{ type: d.type, amount: myr(v) }] : []; });
  const end = useAction(
    () => unwrap(api.POST("/v1/leases/{lease_id}/terminate", { params: { path: { lease_id: lease.id } }, body: {
      termination_date: f.date, reason: f.reason, waive_notice: f.waive, ...(deduction.length ? { deposit_deduction: deduction } : {}),
    } })),
    [["lease", lease.id], ["leases"], ["invoices"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); end.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={end.error} />
      <Field label={t("leases.terminationDate")} error={end.fields["termination_date"]}><Input required type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
      <Field label={t("leases.reason")} error={end.fields["reason"]}><Input required maxLength={500} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      {lease.end_date === null && <label className="check"><input type="checkbox" checked={f.waive} onChange={(e) => setF({ ...f, waive: e.target.checked })} />{t("leases.waiveNotice", { days: lease.notice_days })}</label>}
      {held.map((d) => (
        <Field key={d.type} label={t("leases.keep", { type: t(`leases.depositTypes.${d.type}`), held: formatMoney(d.amount) })} error={end.fields["deposit_deduction"]}>
          <Input inputMode="decimal" placeholder="0.00" value={kept[d.type] ?? ""} onChange={(e) => setKept({ ...kept, [d.type]: e.target.value })} />
        </Field>
      ))}
      <Button type="submit" variant="danger" disabled={end.pending}>{t("leases.terminate")}</Button>
    </form>
  );
}
