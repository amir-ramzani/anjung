import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, unwrap, type Schemas } from "../api/client";
import { STAFF, useAuth } from "../auth/AuthContext";
import { DocumentsSection } from "../components/Documents";
import { Lifecycle } from "../components/Lifecycle";
import { Badge, Button, Empty, ErrorBox, Field, Input, Loading, LoadMore, Page, Select, Table, dateOnly, placeLabel } from "../components/ui";
import { useAction, usePaged } from "../lib/hooks";

type Req = Schemas["MaintenanceRequest"];

/** "Pelangi · D-5-8 › Master", or "Pelangi · shared areas" for a building or common-area request. */
function Where({ request: r }: { request: Req }) {
  const { t } = useTranslation();
  const place = placeLabel(r);
  const shared = r.area === "shared_area" ? t("maintenance.sharedAreas") : null;
  return <>{[place || null, shared].filter(Boolean).join(" · ") || "-"}</>;
}
const CATEGORIES = ["plumbing", "electrical", "appliance", "structural", "pest", "other"] as const;
const PRIORITIES = ["low", "normal", "high", "urgent"] as const;
const STATUSES = ["open", "triaged", "assigned", "in_progress", "resolved", "closed", "cancelled"] as const;
/** The state machine of LLD 5.3. The server enforces it, this only offers the legal next steps. */
const NEXT: Record<string, string[]> = {
  open: ["triaged", "cancelled"], triaged: ["assigned", "cancelled"], assigned: ["in_progress", "cancelled"],
  in_progress: ["resolved"], resolved: ["closed", "in_progress"], closed: [], cancelled: [],
};
export const nextStatuses = (s: string): string[] => NEXT[s] ?? [];

export function MaintenanceList() {
  const { t } = useTranslation();
  const { has } = useAuth();
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState(false);
  const list = usePaged<Req>(["maintenance", status], (cursor) =>
    unwrap(api.GET("/v1/maintenance-requests", { params: { query: { limit: 25, cursor, ...(status ? { status: status as Req["status"] } : {}) } } })));
  return (
    <Page title={t("maintenance.title")} actions={<>
      {has(...STAFF) && (
        <Select aria-label={t("common.status")} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{t("common.all")}</option>{STATUSES.map((s) => <option key={s} value={s}>{t(`status.${s}`)}</option>)}
        </Select>)}
      <Button onClick={() => setOpen(!open)}>{t("maintenance.raise")}</Button>
    </>}>
      {open && <RequestForm onDone={() => setOpen(false)} />}
      <ErrorBox error={list.error} />
      {list.isLoading ? <Loading /> : list.items.length === 0 ? <Empty /> : (
        <Table rows={list.items} rowKey={(r) => r.id} rowTo={(r) => `/maintenance/${r.id}`} columns={[
          { header: t("maintenance.subject"), cell: (r) => <Link className="row-title" to={`/maintenance/${r.id}`}>{r.title}</Link> },
          { header: t("maintenance.where"), cell: (r) => <Where request={r} /> },
          ...(has(...STAFF) ? [{ header: t("maintenance.reportedBy"), cell: (r: Req) => r.reported_by_name ?? "-" }] : []),
          { header: t("maintenance.category"), cell: (r) => t(`maintenance.categories.${r.category}`) },
          { header: t("maintenance.priority"), cell: (r) => t(`maintenance.priorities.${r.priority}`) },
          { header: t("common.status"), cell: (r) => <Badge value={r.status} /> },
          { header: t("maintenance.raised"), cell: (r) => dateOnly(r.created_at) },
        ]} />
      )}
      <LoadMore hasMore={list.hasMore} loading={list.loadingMore} onClick={list.loadMore} />
    </Page>
  );
}

function RequestForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const { has } = useAuth();
  const nav = useNavigate();
  const tenant = !has(...STAFF);
  // a tenant raises a request about a unit they lease now, its shared areas, or the building
  const leases = useQuery({ queryKey: ["leases", "active"], queryFn: () => unwrap(api.GET("/v1/leases", { params: { query: { limit: 20, status: "active" } } })), enabled: tenant });
  const units = useQuery({
    queryKey: ["units-of-leases", leases.data?.items.map((l) => l.unit_id)], enabled: !!leases.data,
    queryFn: () => Promise.all((leases.data?.items ?? []).map((l) => unwrap(api.GET("/v1/units/{unit_id}", { params: { path: { unit_id: l.unit_id } } })))),
  });
  const [f, setF] = useState({ where: "", title: "", description: "", category: "plumbing", priority: "normal" });
  // "where" is "unit:<id>", "shared:<unitId>" or "building:<propertyId>"
  const [kind, id] = f.where.split(":") as [string, string];
  const create = useAction(
    () => unwrap(api.POST("/v1/maintenance-requests", { body: {
      title: f.title, description: f.description, category: f.category as Req["category"], priority: f.priority as Req["priority"],
      ...(kind === "unit" ? { area: "unit" as const, unit_id: id } : kind === "shared" ? { area: "shared_area" as const, unit_id: id } : { area: "shared_area" as const, property_id: id }),
    } })),
    [["maintenance"]], (r) => { onDone(); nav(`/maintenance/${r.id}`); },
  );
  const options = (units.data ?? []).flatMap((u) => [
    { v: `unit:${u.id}`, label: u.label },
    ...(u.parent_unit_id ? [{ v: `shared:${u.parent_unit_id}`, label: `${t("maintenance.sharedAreas")} (${u.label})` }] : []),
    { v: `building:${u.property_id}`, label: `${t("maintenance.building")} (${u.label})` },
  ]);
  const submit = (e: FormEvent) => { e.preventDefault(); create.run(undefined as never); };
  return (
    <form onSubmit={submit} className="card">
      <ErrorBox error={create.error} />
      <Field label={t("maintenance.where")} error={create.fields["unit_id"] ?? create.fields["property_id"]}>
        {tenant ? (
          <Select required value={f.where} onChange={(e) => setF({ ...f, where: e.target.value })}>
            <option value="" />{options.filter((o, i, a) => a.findIndex((x) => x.v === o.v) === i).map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </Select>
        ) : (
          <Input required placeholder="unit:<id> or building:<id>" value={f.where} onChange={(e) => setF({ ...f, where: e.target.value })} />
        )}
      </Field>
      <Field label={t("maintenance.subject")} error={create.fields["title"]}><Input required maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field label={t("maintenance.description")} error={create.fields["description"]}><Input required maxLength={4000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label={t("maintenance.category")}>
        <Select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c} value={c}>{t(`maintenance.categories.${c}`)}</option>)}</Select>
      </Field>
      <Field label={t("maintenance.priority")}>
        <Select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>{PRIORITIES.map((c) => <option key={c} value={c}>{t(`maintenance.priorities.${c}`)}</option>)}</Select>
      </Field>
      <Button type="submit" disabled={create.pending}>{t("common.save")}</Button>
    </form>
  );
}

export function MaintenanceDetail() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { has } = useAuth();
  const staff = has(...STAFF);
  const req = useQuery({ queryKey: ["maintenance", "one", id], queryFn: () => unwrap(api.GET("/v1/maintenance-requests/{request_id}", { params: { path: { request_id: id } } })) });
  const comments = usePaged<Schemas["Comment"]>(["comments", id], (cursor) =>
    unwrap(api.GET("/v1/maintenance-requests/{request_id}/comments", { params: { path: { request_id: id }, query: { limit: 50, cursor } } })));
  const move = useAction((status: string) => unwrap(api.PATCH("/v1/maintenance-requests/{request_id}", { params: { path: { request_id: id } }, body: { status: status as Req["status"] } })),
    [["maintenance"]]);
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const post = useAction(() => unwrap(api.POST("/v1/maintenance-requests/{request_id}/comments", { params: { path: { request_id: id } }, body: { body, internal } })),
    [["comments", id]], () => { setBody(""); setInternal(false); });
  const { me } = useAuth();
  const [assignee, setAssignee] = useState("");
  // only an administrator can list staff. A property manager can assign a request to themselves.
  const staffList = useQuery({
    queryKey: ["users", "assignees"], enabled: staff && has("org_admin"),
    queryFn: () => unwrap(api.GET("/v1/users", { params: { query: { limit: 100 } } })),
  });
  const nav = useNavigate();
  const assign = useAction((userId: string) => unwrap(api.PATCH("/v1/maintenance-requests/{request_id}", { params: { path: { request_id: id } }, body: { assigned_to: userId } })), [["maintenance"]]);
  if (req.isLoading) return <Loading />;
  const r = req.data;
  if (!r) return <ErrorBox error={req.error} />;
  const closed = r.status === "closed" || r.status === "cancelled";
  const submit = (e: FormEvent) => { e.preventDefault(); post.run(undefined as never); };
  return (
    <Page title={r.title} actions={staff && nextStatuses(r.status).filter((s) => s !== "assigned").map((s) => (
      <Button key={s} variant={s === "cancelled" ? "danger" : "secondary"} disabled={move.pending} onClick={() => move.run(s)}>{t(`maintenance.moveTo.${s}`)}</Button>))}>
      <ErrorBox error={move.error ?? assign.error} />
      {staff && !closed && (r.status === "triaged" || r.status === "assigned") && (
        <form onSubmit={(e) => { e.preventDefault(); assign.run(assignee || me?.user_id || ""); }} className="card">
          {has("org_admin") ? (
            <Field label={t("maintenance.assignTo")} error={assign.fields["assigned_to"]}>
              <Select required value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="" />{(staffList.data?.items ?? []).filter((u) => u.status !== "disabled").map((u) => <option key={u.user_id} value={u.user_id}>{u.name || u.email}</option>)}
              </Select>
            </Field>
          ) : <p className="muted">{t("maintenance.assignSelfHelp")}</p>}
          <Button type="submit" disabled={assign.pending || (has("org_admin") && !assignee)}>{has("org_admin") ? t("maintenance.moveTo.assigned") : t("maintenance.assignMe")}</Button>
          {r.assigned_to && <small className="muted">{t("maintenance.assignedTo")}: {r.assigned_to === me?.user_id ? t("maintenance.you") : r.assigned_to.slice(0, 8)}</small>}
        </form>
      )}
      <dl className="facts">
        <dt>{t("common.status")}</dt><dd><Badge value={r.status} /></dd>
        <dt>{t("maintenance.category")}</dt><dd>{t(`maintenance.categories.${r.category}`)}</dd>
        <dt>{t("maintenance.priority")}</dt><dd>{t(`maintenance.priorities.${r.priority}`)}</dd>
        <dt>{t("maintenance.where")}</dt><dd><Where request={r} /></dd>
        <dt>{t("maintenance.reportedBy")}</dt><dd>{r.reported_by_name ?? "-"} · {dateOnly(r.created_at)}</dd>
        <dt>{t("maintenance.assignedTo")}</dt><dd>{r.assigned_to_name ?? <span className="muted">{t("maintenance.unassigned")}</span>}</dd>
      </dl>
      <p>{r.description}</p>
      <DocumentsSection ownerType="maintenance_request" ownerId={r.id} types={["maintenance_photo", "other"]} readOnly={closed} />
      <h2>{t("maintenance.comments")}</h2>
      {comments.items.length === 0 ? <Empty /> : comments.items.map((c) => (
        <article key={c.id} className={`comment ${c.internal ? "internal" : ""}`}>
          <small>{c.author_role} · {dateOnly(c.created_at)}{c.internal ? ` · ${t("maintenance.internal")}` : ""}</small>
          <p>{c.body}</p>
        </article>
      ))}
      <LoadMore hasMore={comments.hasMore} loading={comments.loadingMore} onClick={comments.loadMore} />
      {!closed && (
        <form onSubmit={submit} className="card">
          <ErrorBox error={post.error} />
          <Field label={t("maintenance.addComment")} error={post.fields["body"]}><Input required maxLength={4000} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
          {staff && <label className="check"><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} />{t("maintenance.internalNote")}</label>}
          <Button type="submit" disabled={post.pending}>{t("maintenance.send")}</Button>
        </form>
      )}
      {has("org_admin") && closed && (
        <Lifecycle name={r.title}
          preview={() => unwrap(api.GET("/v1/maintenance-requests/{request_id}/deletion", { params: { path: { request_id: id } } }))}
          remove={(reason) => unwrap(api.DELETE("/v1/maintenance-requests/{request_id}", { params: { path: { request_id: id }, query: { reason } } }))}
          invalidate={[["maintenance"]]} onDeleted={() => nav("/maintenance")} />
      )}
    </Page>
  );
}
