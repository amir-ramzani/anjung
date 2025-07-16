import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "../api/client";
import { blockerText } from "../lib/errors";
import { useAction } from "../lib/hooks";
import { Button, ErrorBox, Field, Input, Loading } from "./ui";

type Preview = Schemas["DeletionPreview"];
type Result = Schemas["DeletionResult"];

interface Props {
  /** What the person types to confirm a delete, e.g. the property name. */
  name: string;
  /** For records that can be switched off: whether it is on now. Leave out for delete-only records. */
  active?: boolean;
  deactivate?: () => Promise<unknown>;
  reactivate?: () => Promise<unknown>;
  preview: () => Promise<Preview>;
  remove: (reason: string) => Promise<Result>;
  /** Queries to refresh after any change. */
  invalidate: QueryKey[];
  onDeleted: (r: Result) => void;
  /** "Delete draft" etc. Defaults to "Delete". */
  deleteLabel?: string;
}

/** Blockers no action of the user's can clear: the record is kept (financial history, an erased tenant). */
const PERMANENT = new Set(["lease_history", "not_draft", "already_erased"]);

/** Deactivate, reactivate and delete, the same in every module (contract v0.9.0). The delete preview is read when
 * the section shows: Delete is offered only when it would work, otherwise the reasons are shown in its place.
 * Delete then shows what will happen (removed or erased), and asks for a reason and the typed name. */
export function Lifecycle(p: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const preview = useQuery({ queryKey: ["deletion", p.name, p.invalidate], queryFn: p.preview, gcTime: 0, staleTime: 0 });
  const blocked = preview.data?.mode === "blocked" ? preview.data.blockers ?? [] : null;
  const kept = blocked?.some((b) => PERMANENT.has(b.code));
  const toggle = useAction(() => (p.active ? p.deactivate!() : p.reactivate!()), p.invalidate);
  const switchable = p.active !== undefined && p.deactivate && p.reactivate;
  const heading = useId();
  return (
    <section aria-labelledby={heading} className="card lifecycle">
      <h2 id={heading}>{t("lifecycle.title")}</h2>
      {switchable && (
        <>
          <p className="muted">{t(p.active ? "lifecycle.deactivateHelp" : "lifecycle.inactiveHelp")}</p>
          <ErrorBox error={toggle.error} />
        </>
      )}
      <div className="actions">
        {switchable && (
          <Button variant="secondary" disabled={toggle.pending} onClick={() => toggle.run(undefined as never)}>
            {t(p.active ? "lifecycle.deactivate" : "lifecycle.reactivate")}
          </Button>
        )}
        {!blocked && !preview.isLoading && (
          <Button variant="danger" aria-expanded={open} onClick={() => setOpen(!open)}>{p.deleteLabel ?? t("lifecycle.delete")}</Button>
        )}
      </div>
      {blocked && (
        <div className="muted">
          <p style={{ marginBottom: 0 }}>{t(kept ? "lifecycle.kept" : "lifecycle.blocked")}</p>
          <ul>{blocked.map((b) => <li key={b.code}>{blockerText(b)}</li>)}</ul>
        </div>
      )}
      {open && !blocked && <DeletePanel {...p} onCancel={() => setOpen(false)} />}
    </section>
  );
}

function DeletePanel(p: Props & { onCancel: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const preview = useQuery({ queryKey: ["deletion", p.name, p.invalidate], queryFn: p.preview, gcTime: 0, staleTime: 0 });
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const del = useAction(() => p.remove(reason.trim()), [], async (r) => {
    await Promise.all(p.invalidate.map((k) => qc.invalidateQueries({ queryKey: k })));
    p.onDeleted(r);
  });
  if (preview.isLoading) return <Loading />;
  const v = preview.data;
  if (!v) return <ErrorBox error={preview.error} />;
  const matches = typed.trim().toLowerCase() === p.name.trim().toLowerCase();
  const submit = (e: FormEvent) => { e.preventDefault(); if (matches && reason.trim().length >= 3) del.run(undefined as never); };
  const affects = Object.entries(v.affects ?? {}).filter(([, n]) => n > 0);
  return (
    <div className="delete-panel" role="region" aria-label={t("lifecycle.deleteRegion")}>
      {v.mode === "blocked" ? (
        <>
          <p><strong>{t("lifecycle.blocked")}</strong></p>
          <ul>{(v.blockers ?? []).map((b) => <li key={b.code}>{blockerText(b)}</li>)}</ul>
          <Button variant="secondary" onClick={p.onCancel}>{t("common.cancel")}</Button>
        </>
      ) : (
        <form onSubmit={submit}>
          <p><strong>{t(`lifecycle.mode.${v.mode}`)}</strong></p>
          {v.note && <p className="muted">{v.note}</p>}
          {affects.length > 0 && (
            <p className="muted">{t("lifecycle.alsoGoes")} {affects.map(([k, n]) => t(`lifecycle.affects.${k}`, { count: n, defaultValue: `${n} ${k}` })).join(", ")}</p>
          )}
          <ErrorBox error={del.error} />
          <Field label={t("lifecycle.reason")} error={del.fields["reason"]}>
            <Input required minLength={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <Field label={t("lifecycle.typeName", { name: p.name })}>
            <Input required autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" variant="danger" disabled={!matches || reason.trim().length < 3 || del.pending}>
              {t(v.mode === "erase" ? "lifecycle.confirmErase" : "lifecycle.confirmDelete")}
            </Button>
            <Button type="button" variant="secondary" onClick={p.onCancel}>{t("common.cancel")}</Button>
          </div>
        </form>
      )}
    </div>
  );
}
