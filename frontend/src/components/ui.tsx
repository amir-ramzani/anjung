import { cloneElement, isValidElement, useEffect, type ReactNode, type ComponentProps, type SelectHTMLAttributes, type ButtonHTMLAttributes, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import i18n from "../i18n";
import { setPageTitle } from "../lib/brand";
import { issueText } from "../lib/errors";
import { formatDate } from "../lib/dates";

export function Page({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => { setPageTitle(title); return () => setPageTitle(""); }, [title]);
  return (
    <section>
      <header className="page-head">
        {/* focusable from script only: the layout moves focus here after each navigation */}
        <h1 tabIndex={-1}>{title}</h1>
        <div className="actions">{actions}</div>
      </header>
      {children}
    </section>
  );
}

export function Loading() {
  const { t } = useTranslation();
  return <p role="status" className="muted">{t("common.loading")}</p>;
}

export function Empty({ children }: { children?: ReactNode }) {
  const { t } = useTranslation();
  return <p className="muted empty">{children ?? t("common.empty")}</p>;
}

/** Shows an API error. Field errors are shown next to the inputs by <Field>, so only the rest is listed here. */
/** The API's message in the user's language when the app has a translation for its code (and `params`), else the
 * API's English message. Keys: `errors.<code>.<scope>` or `errors.<code>`. */
export function errorText(e: ApiError): string {
  const scope = e.params?.scope;
  const key = [scope ? `errors.${e.code}.${scope}` : null, `errors.${e.code}`].find((k) => k && i18n.exists(k) && typeof i18n.t(k) === "string");
  if (!key) return e.message;
  const values: Record<string, string | number> = { ...e.params };
  for (const k of ["date", "until"]) if (typeof values[k] === "string") values[k] = formatDate(values[k] as string);
  return i18n.t(key, values);
}

export function ErrorBox({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  const e = error instanceof ApiError ? error : null;
  const generic = e ? (e.code === "validation_error" && e.details.length ? t("common.checkFields") : errorText(e)) : t("common.unexpected");
  return (
    <div role="alert" className="error">
      {generic}
      {e?.requestId && <small> ({e.requestId})</small>}
      {/* an issue about the whole form (e.g. end date before start date) has no input to sit next to */}
      {e?.details.filter((d) => d.field === "body").map((d, i) => <div key={i}>{issueText(d)}</div>)}
    </div>
  );
}

export function Field({ label, error, children }: { label: string; error?: string | undefined; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {/* screen readers say "invalid" on a field with an error */}
      {error && isValidElement<{ "aria-invalid"?: boolean }>(children) ? cloneElement(children, { "aria-invalid": true }) : children}
      {error && <small role="alert" className="error-text">{error}</small>}
    </label>
  );
}

export const Input = (p: ComponentProps<"input">) => <input {...p} />;
export const Select = (p: SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} />;

export function Button({ variant = "primary", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  return <button {...p} className={`btn ${variant} ${p.className ?? ""}`} />;
}

export function Badge({ value, tone }: { value: string; tone?: "good" | "warn" | "bad" | "neutral" }) {
  const auto: Record<string, "good" | "warn" | "bad" | "neutral"> = {
    paid: "good", active: "good", clean: "good", completed: "good", resolved: "good", closed: "neutral",
    open: "warn", partially_paid: "warn", draft: "neutral", invited: "neutral", triaged: "warn", assigned: "warn", in_progress: "warn",
    occupied: "good", vacant: "neutral", maintenance: "warn", inactive: "neutral", disabled: "bad", unmatched: "warn", reversed: "neutral",
    sent: "good", delivered: "good", read: "good", queued: "neutral", suppressed: "warn", succeeded: "good", running: "warn", degraded: "warn", completed_with_errors: "warn", ok: "good",
    overdue: "bad", infected: "bad", scanning: "warn", pending_upload: "neutral", void: "neutral", terminated: "neutral", expired: "neutral", failed: "bad", suspended: "bad", cancelled: "neutral",
  };
  const { t } = useTranslation();
  return <span className={`badge ${tone ?? auto[value] ?? "neutral"}`}>{t(`status.${value}`, { defaultValue: value })}</span>;
}

/** "Pelangi · D-5-8 › Master": property, then the unit, then the room (a room carries its unit's label). */
export function placeLabel(x: { property_name?: string | null; unit_label?: string | null; parent_unit_label?: string | null; unit_type?: string | null }): string {
  const label = x.unit_type === "parking" && x.unit_label ? i18n.t("units.bayLabel", { label: x.unit_label }) : x.unit_label;
  const unit = [x.parent_unit_label, label].filter(Boolean).join(" › ");
  return [x.property_name, unit].filter(Boolean).join(" · ");
}

// Clicks on these keep their own behaviour inside a clickable row.
const INTERACTIVE = "a, button, input, select, textarea, label, summary";

export function Table<T>({ rows, columns, rowKey, rowTo, rowClass }: {
  rows: T[]; columns: { header: string; cell: (r: T) => ReactNode }[]; rowKey: (r: T) => string;
  /** Extra class per row, e.g. "child-row" to nest a row under the one above it. */
  rowClass?: (r: T) => string | undefined;
  /** Makes the whole row open this path. Keep a real link in the row too: it is what keyboards and screen readers use. */
  rowTo?: (r: T) => string;
}) {
  const nav = useNavigate();
  const open = (e: MouseEvent<HTMLTableRowElement>, to: string) => {
    if ((e.target as HTMLElement).closest(INTERACTIVE) || window.getSelection()?.toString()) return; // let text be selected
    if (e.metaKey || e.ctrlKey) window.open(to, "_blank", "noopener");
    else nav(to);
  };
  return (
    <div className="table-wrap">
      {/* data-label lets narrow screens show each row as a stacked card (see styles.css) */}
      <table className="stack">
        <thead><tr>{columns.map((c) => <th key={c.header} scope="col">{c.header}</th>)}</tr></thead>
        <tbody>{rows.map((r) => {
          const to = rowTo?.(r);
          return (
            <tr key={rowKey(r)} className={[to && "row-link", rowClass?.(r)].filter(Boolean).join(" ") || undefined} onClick={to ? (e) => open(e, to) : undefined}>
              {columns.map((c) => <td key={c.header} data-label={c.header || undefined}>{c.cell(r)}</td>)}
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );
}

export function LoadMore({ hasMore, loading, onClick }: { hasMore: boolean; loading: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  if (!hasMore) return null;
  return <Button variant="secondary" onClick={onClick} disabled={loading}>{loading ? t("common.loading") : t("common.loadMore")}</Button>;
}

/** A date for display, DD-MM-YYYY (src/lib/dates.ts). */
export const dateOnly = (iso?: string | null): string => formatDate(iso);
