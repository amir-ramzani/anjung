import i18n from "../i18n";
import { formatDate } from "./dates";
import { formatMoney } from "./money";

type Params = Record<string, string | number> | null | undefined;

/** Values ready to show: ISO dates as DD-MM-YYYY, sen as money, unit kinds in the user's language. */
function values(params: Params): Record<string, string | number> {
  const out: Record<string, string | number> = { ...(params ?? {}) };
  if (typeof out.date === "string") out.date = formatDate(out.date);
  if (typeof out.amount_minor === "number") out.amount = formatMoney({ amount_minor: out.amount_minor, currency: "MYR" });
  if (typeof out.what === "string") out.what = i18n.t(`blockers.what.${out.what}`, { defaultValue: out.what });
  if (typeof out.type === "string") out.type = i18n.t(`leases.depositTypes.${out.type}`, { defaultValue: out.type });
  return out;
}

/** A field issue in the user's language (contract v0.16.0): `fieldErrors.<code>`, else the API's English. */
export function issueText(d: { issue: string; code?: string; params?: Params }): string {
  const key = d.code ? `fieldErrors.${d.code}` : null;
  return key && i18n.exists(key) ? i18n.t(key, values(d.params)) : d.issue;
}

/** A delete blocker in the user's language: `blockers.<code>`, else the API's English. */
// blockers whose sentence has values in it: without `params` (an older API) the English message is shown
const NEEDS_PARAMS = new Set(["unpaid", "credit", "deposits_held", "lease_history", "maintenance_history"]);

export function blockerText(b: { code: string; message: string; params?: Params }): string {
  if (NEEDS_PARAMS.has(b.code) && !b.params) return b.message;
  const v = values(b.params);
  const base = `blockers.${b.code}`;
  // lease_history names the units when it is about a property
  const key = b.code === "lease_history" && b.params?.what === "property" ? `${base}_property` : base;
  if (!i18n.exists(key)) return b.message;
  const text = i18n.t(key, { ...v, count: typeof b.params?.count === "number" ? b.params.count : undefined });
  return typeof b.params?.more === "number" && b.params.more > 0 ? `${text} ${i18n.t("blockers.more", { count: b.params.more })}` : text;
}
