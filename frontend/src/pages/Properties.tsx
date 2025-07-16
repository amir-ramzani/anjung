import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { DocumentsSection } from "../components/Documents";
import { Lifecycle } from "../components/Lifecycle";
import { EditProperty, EditUnit } from "./forms";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, Select, Table } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";
import { formatMoney, myr, parseMoney } from "../lib/money";

type Property = Schemas["Property"];
type Unit = Schemas["Unit"];
const TYPES = ["condominium", "apartment", "landed", "shoplot", "room", "other"] as const;

type User = Schemas["User"];

/** Staff who can manage a property (admins only can list them). Shared query key with maintenance assignees. */
function useManagers() {
  const { has } = useAuth();
  const users = useQuery({
    queryKey: ["users", "assignees"], enabled: has("org_admin"),
    queryFn: () => unwrap(api.GET("/v1/users", { params: { query: { limit: 100 } } })),
  });
  const all = users.data?.items ?? [];
  const managers = all.filter((u: User) => u.status !== "disabled" && u.roles.some((r) => r === "org_admin" || r === "property_manager"));
  const nameOf = (id?: string | null) => {
    const u = all.find((x: User) => x.user_id === id);
    return u ? u.name || u.email : id ? "-" : "";
  };
  return { managers, nameOf };
}

/** Admin only: who manages this property. A manager sees only the properties they manage. */
function ManagedBy({ property }: { property: Property }) {
  const { t } = useTranslation();
  const { managers } = useManagers();
  const [picked, setPicked] = useState(property.manager_id ?? "");
  const [saved, setSaved] = useState(false);
  const save = useAction((managerId: string) => unwrap(api.PATCH("/v1/properties/{property_id}", {
    params: { path: { property_id: property.id } }, body: { manager_id: managerId },
  })), [["property", property.id], ["properties"]], () => setSaved(true));
  return (
    <div className="managed-by">
      <Field label={t("properties.managedBy")}>
        <Select value={picked} onChange={(e) => { setPicked(e.target.value); setSaved(false); }}>
          {!property.manager_id && <option value="" />}
          {managers.map((u) => <option key={u.user_id} value={u.user_id}>{u.name || u.email}{u.roles.includes("org_admin") ? ` (${t("properties.admin")})` : ""}</option>)}
        </Select>
      </Field>
      <Button variant="secondary" disabled={!picked || picked === property.manager_id || save.pending} onClick={() => save.run(picked)}>{t("properties.changeManager")}</Button>
      {saved && <span className="success" role="status">{t("common.saved")}</span>}
      <ErrorBox error={save.error} />
    </div>
  );
}

/** A property manager's units against the limit an admin set; null for admins (no limit of their own). */
export function usePropertyQuota() {
  const { has } = useAuth();
  const manager = has("property_manager") && !has("org_admin");
  const q = useQuery({ queryKey: ["properties", "quota"], enabled: manager,
    queryFn: () => unwrap(api.GET("/v1/properties", { params: { query: { limit: 1, status: "active" } } })) });
  return q.data?.quota ?? null;
}

/** What the badge on the Properties page says: the tighter of the user's own unit limit and the company's plan
 * (contract v0.13.0). Admins have no limit of their own, so only the company's applies to them. */
export function unitAllowance(
  own: { used: number; limit?: number | null } | null,
  plan: { unit_limit?: number | null; units_used?: number } | null | undefined,
): { kind: "own" | "company" | "unlimited"; used: number; limit: number | null; full: boolean } | null {
  const ownLeft = own && own.limit != null ? own.limit - own.used : Infinity;
  const companyLeft = plan?.unit_limit != null ? plan.unit_limit - (plan.units_used ?? 0) : Infinity;
  if (ownLeft === Infinity && companyLeft === Infinity) return own ? { kind: "unlimited", used: own.used, limit: null, full: false } : null;
  if (ownLeft <= companyLeft) return { kind: "own", used: own!.used, limit: own!.limit ?? null, full: ownLeft <= 0 };
  return { kind: "company", used: plan!.units_used ?? 0, limit: plan!.unit_limit ?? null, full: companyLeft <= 0 };
}

export function PropertyList() {
  const { t } = useTranslation();
  const { has } = useAuth();
  const { nameOf } = useManagers();
  const quota = usePropertyQuota();
  const { me } = useAuth();
  const allowance = unitAllowance(quota, me?.plan);
  const full = !!allowance?.full;
  const list = usePaged<Property>(["properties"], (cursor) =>
    unwrap(api.GET("/v1/properties", { params: { query: { limit: 25, cursor } } })),
  );
  const [open, setOpen] = useState(false);
  return (
    <Page title={t("properties.title")} actions={<>
      {allowance && <span className={`badge ${full ? "warn" : "neutral"}`} title={t(allowance.kind === "company" ? "properties.companyHelp" : "properties.quotaHelp")}>
        {allowance.kind === "unlimited" ? t("properties.quotaNone", { used: allowance.used })
          : allowance.kind === "company" ? t("properties.companyQuota", { used: allowance.used, limit: allowance.limit })
          : t("properties.quota", { used: allowance.used, limit: allowance.limit })}
      </span>}
      <Button onClick={() => setOpen(!open)}>{t("properties.add")}</Button>
    </>}>
      {full && <p className="notice" role="status">{allowance?.kind === "company"
        ? t("properties.companyFull", { used: allowance.used, limit: allowance.limit })
        : t("properties.quotaFull", { limit: allowance?.limit })}</p>}
      {open && <PropertyForm onDone={() => setOpen(false)} />}
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(p) => p.id} rowTo={(p) => `/properties/${p.id}`} columns={[
          { header: t("properties.name"), cell: (p) => <Link className="row-title" to={`/properties/${p.id}`}>{p.name}</Link> },
          { header: t("properties.type"), cell: (p) => t(`properties.types.${p.type}`) },
          { header: t("properties.units"), cell: (p) => <UnitCount p={p} /> },
          ...(has("org_admin") ? [{ header: t("properties.managedBy"), cell: (p: Property) => nameOf(p.manager_id) || "-" }] : []),
          { header: t("common.status"), cell: (p) => <Badge value={p.status} /> },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

/** Units that are on (what the plan counts), and how many are off, if any (contract v0.13.2). */
function UnitCount({ p }: { p: Property }) {
  const { t } = useTranslation();
  const all = p.unit_count ?? 0;
  const active = p.units_active ?? all;
  return all > active
    ? <span title={t("properties.unitsHelp")}>{t("properties.unitsOff", { active, off: all - active })}</span>
    : <>{active}</>;
}

function PropertyForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [f, setF] = useState({ name: "", type: "condominium", line1: "", city: "", state: "", postcode: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const create = useAction(
    () => unwrap(api.POST("/v1/properties", { body: {
      name: f.name, type: f.type as Property["type"],
      address: { line1: f.line1, city: f.city, state: f.state, postcode: f.postcode, country: "MY" },
    } })),
    [["properties"]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); create.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={create.error} />
      <Field label={t("properties.name")} error={create.fields["name"]}><Input required value={f.name} onChange={set("name")} /></Field>
      <Field label={t("properties.type")}>
        <Select value={f.type} onChange={set("type")}>{TYPES.map((x) => <option key={x} value={x}>{t(`properties.types.${x}`)}</option>)}</Select>
      </Field>
      <Field label={t("address.line1")} error={create.fields["address.line1"]}><Input required value={f.line1} onChange={set("line1")} /></Field>
      <Field label={t("address.city")} error={create.fields["address.city"]}><Input required value={f.city} onChange={set("city")} /></Field>
      <Field label={t("address.state")} error={create.fields["address.state"]}><Input required value={f.state} onChange={set("state")} /></Field>
      <Field label={t("address.postcode")} error={create.fields["address.postcode"]}>
        <Input required inputMode="numeric" pattern="[0-9]{5}" value={f.postcode} onChange={set("postcode")} />
      </Field>
      <Button type="submit" disabled={create.pending}>{t("common.save")}</Button>
    </form>
  );
}

export function PropertyDetail() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { has } = useAuth();
  const prop = useQuery({ queryKey: ["property", id], queryFn: () => unwrap(api.GET("/v1/properties/{property_id}", { params: { path: { property_id: id } } })) });
  const units = usePaged<Unit>(["units", id], (cursor) =>
    unwrap(api.GET("/v1/properties/{property_id}/units", { params: { path: { property_id: id }, query: { limit: 100, cursor } } })),
  );
  const [adding, setAdding] = useState<"unit" | "room" | "bay" | null>(null);
  const [editing, setEditing] = useState(false);
  const [editUnit, setEditUnit] = useState<Unit | null>(null);
  // one unit's documents at a time, opened from its row (they live on the unit, not the property)
  const [docsFor, setDocsFor] = useState<Unit | null>(null);
  useEffect(() => {
    if (docsFor) document.getElementById("docs-unit")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [docsFor]);
  const nav = useNavigate();
  const pathId = { params: { path: { property_id: id } } };
  if (prop.isLoading) return <Loading />;
  if (!prop.data) return <ErrorBox error={prop.error} />;
  const wholeUnits = units.items.filter((u) => u.type === "whole_unit");
  const spaces = units.items.filter((u) => u.type !== "parking");
  const bays = units.items.filter((u) => u.type === "parking");
  const labelOf = (uid?: string | null) => units.items.find((u) => u.id === uid)?.label;
  return (
    <Page title={prop.data.name} actions={has("org_admin", "property_manager") && prop.data.status === "active" && (<>
      <Button variant="secondary" onClick={() => setEditing(!editing)}>{t("common.edit")}</Button>
      <Button onClick={() => setAdding("unit")}>{t("units.addUnit")}</Button>
      <Button variant="secondary" onClick={() => setAdding("room")}>{t("units.addRoom")}</Button>
      <Button variant="secondary" onClick={() => setAdding("bay")}>{t("units.addBay")}</Button>
    </>)}>
      {prop.data.status === "inactive" && <p><Badge value="inactive" /> <span className="muted">{t("lifecycle.inactiveHelp")}</span></p>}
      <p className="muted">{prop.data.address.line1}, {prop.data.address.postcode} {prop.data.address.city}</p>
      {has("org_admin") && prop.data.status === "active" && <ManagedBy property={prop.data} />}
      {editing && <EditProperty property={prop.data} onDone={() => setEditing(false)} />}
      {editUnit && <EditUnit unit={editUnit} propertyId={id} wholeUnits={wholeUnits} onDone={() => setEditUnit(null)} />}
      {adding && <UnitForm propertyId={id} kind={adding} wholeUnits={wholeUnits} onDone={() => setAdding(null)} />}
      <ErrorBox error={units.error} />
      {units.isLoading ? <Loading /> : spaces.length === 0 ? <Empty /> : (
        <Table rows={spaces} rowKey={(u) => u.id}
          rowClass={(u) => u.type === "room" ? "child-row" : u.letting_mode === "rooms" ? "parent-row" : undefined} columns={[
          { header: t("units.label"), cell: (u) => u.type === "room" ? <span><span className="room-mark" aria-hidden="true">↳</span>{u.label}</span> : u.label },
          { header: t("units.kind"), cell: (u) => u.type === "room" ? t("units.room") : t(`units.mode.${u.letting_mode ?? "whole"}`) },
          { header: t("units.rent"), cell: (u) => formatMoney(u.default_rent) },
          { header: t("common.status"), cell: (u) => <Badge value={u.status} /> },
          { header: "", cell: (u) => has("org_admin", "property_manager") && prop.data.status === "active" ? (<>
            <Button variant="secondary" onClick={() => setEditUnit(u)}>{t("common.edit")}</Button>
            {u.type === "whole_unit" && u.status !== "inactive" && <ModeToggle unit={u} propertyId={id} />}
            <Button variant="secondary" aria-expanded={docsFor?.id === u.id} onClick={() => setDocsFor(docsFor?.id === u.id ? null : u)}>{t("docs.title")}</Button>
          </>) : null },
        ]} />
      )}
      <LoadMore hasMore={units.hasMore} loading={units.loadingMore} onClick={units.loadMore} />
      {!units.isLoading && (
        <section aria-labelledby="bays">
          <h2 id="bays">{t("units.bays")}</h2>
          {bays.length === 0 ? <p className="muted">{t("units.noBays")}</p> : (
            <Table rows={bays} rowKey={(u) => u.id} columns={[
              { header: t("units.bay"), cell: (u) => u.label },
              { header: t("units.allocated"), cell: (u) => u.parent_unit_id ? t("units.withUnit", { label: labelOf(u.parent_unit_id) ?? "?" }) : t("units.propertyBay") },
              { header: t("units.note"), cell: (u) => u.note ?? "" },
              { header: t("units.rent"), cell: (u) => formatMoney(u.default_rent) },
              { header: t("common.status"), cell: (u) => <Badge value={u.status} /> },
              { header: "", cell: (u) => has("org_admin", "property_manager") && prop.data.status === "active" ? (
                <Button variant="secondary" onClick={() => setEditUnit(u)}>{t("common.edit")}</Button>
              ) : null },
            ]} />
          )}
        </section>
      )}
      {docsFor && (
        <DocumentsSection key={docsFor.id} ownerType="unit" ownerId={docsFor.id} title={t("docs.forUnit", { label: docsFor.label })}
          types={["inspection_report", "other"]} readOnly={prop.data.status !== "active" || docsFor.status === "inactive"} />
      )}
      <DocumentsSection ownerType="property" ownerId={id} types={["inspection_report", "other"]} readOnly={prop.data.status !== "active"} />
      {has("org_admin", "property_manager") && (
        <Lifecycle name={prop.data.name} active={prop.data.status === "active"}
          deactivate={() => unwrap(api.POST("/v1/properties/{property_id}/deactivate", pathId))}
          reactivate={() => unwrap(api.POST("/v1/properties/{property_id}/reactivate", pathId))}
          preview={() => unwrap(api.GET("/v1/properties/{property_id}/deletion", pathId))}
          remove={(reason) => unwrap(api.DELETE("/v1/properties/{property_id}", { params: { path: { property_id: id }, query: { reason } } }))}
          invalidate={[["property", id], ["properties"], ["units", id]]} onDeleted={() => nav("/properties")} />
      )}
    </Page>
  );
}

function ModeToggle({ unit, propertyId }: { unit: Unit; propertyId: string }) {
  const { t } = useTranslation();
  const next = unit.letting_mode === "rooms" ? "whole" : "rooms";
  const change = useAction(() => unwrap(api.PATCH("/v1/units/{unit_id}", { params: { path: { unit_id: unit.id } }, body: { letting_mode: next } })),
    [["units", propertyId]]);
  return (
    <>
      <Button variant="secondary" disabled={change.pending} onClick={() => change.run(undefined as never)}>{t(`units.switchTo.${next}`)}</Button>
      {change.error != null && <ErrorBox error={change.error} />}
    </>
  );
}

function UnitForm({ propertyId, kind, wholeUnits, onDone }: { propertyId: string; kind: "unit" | "room" | "bay"; wholeUnits: Unit[]; onDone: () => void }) {
  const { t } = useTranslation();
  const roomHosts = wholeUnits.filter((u) => u.letting_mode === "rooms");
  const [f, setF] = useState({ label: "", rent: "", parent: kind === "room" ? roomHosts[0]?.id ?? "" : "", mode: "whole", note: "" });
  const rent = parseMoney(f.rent);
  const create = useAction(
    () => unwrap(api.POST("/v1/properties/{property_id}/units", { params: { path: { property_id: propertyId } }, body: {
      label: f.label, default_rent: myr(rent ?? 0),
      ...(kind === "room" ? { type: "room" as const, parent_unit_id: f.parent }
        : kind === "bay" ? { type: "parking" as const, ...(f.parent ? { parent_unit_id: f.parent } : {}), ...(f.note.trim() ? { note: f.note.trim() } : {}) }
        : { letting_mode: f.mode as "whole" | "rooms" }),
    } })),
    [["units", propertyId], ["property", propertyId]], onDone,
  );
  const submit = (e: FormEvent) => { e.preventDefault(); if (rent !== null && (kind !== "room" || f.parent)) create.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={create.error} />
      {kind === "room" && (roomHosts.length === 0 ? <p className="notice">{t("units.noRoomHost")}</p> : (
        <Field label={t("units.parent")} error={create.fields["parent_unit_id"]}>
          <Select value={f.parent} onChange={(e) => setF({ ...f, parent: e.target.value })}>{roomHosts.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}</Select>
        </Field>
      ))}
      <Field label={kind === "bay" ? t("units.bay") : t("units.label")} error={create.fields["label"]}><Input required maxLength={50} value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
      <Field label={t("units.rent")} error={create.fields["default_rent.amount_minor"] ?? (f.rent && rent === null ? t("common.invalidAmount") : undefined)}>
        <Input required inputMode="decimal" placeholder={kind === "bay" ? "150.00" : "1500.00"} value={f.rent} onChange={(e) => setF({ ...f, rent: e.target.value })} />
      </Field>
      {kind === "bay" && (<>
        <Field label={t("units.allocated")} error={create.fields["parent_unit_id"]}>
          <Select value={f.parent} onChange={(e) => setF({ ...f, parent: e.target.value })}>
            <option value="">{t("units.propertyBay")}</option>
            {wholeUnits.filter((u) => u.status !== "inactive").map((u) => <option key={u.id} value={u.id}>{t("units.withUnit", { label: u.label })}</option>)}
          </Select>
        </Field>
        <p className="muted">{t("units.bayHelp")}</p>
        <Field label={t("units.note")}><Input maxLength={200} placeholder={t("units.notePlaceholder")} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      </>)}
      {kind === "unit" && (
        <Field label={t("units.lettingMode")}>
          <Select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}>
            <option value="whole">{t("units.mode.whole")}</option><option value="rooms">{t("units.mode.rooms")}</option>
          </Select>
        </Field>
      )}
      <Button type="submit" disabled={create.pending || rent === null || (kind === "room" && !f.parent)}>{t("common.save")}</Button>
    </form>
  );
}
