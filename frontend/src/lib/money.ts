/** Money is an integer in minor units (sen) end to end. No floats are used to calculate or to parse. */
export interface Money {
  amount_minor: number;
  currency: "MYR";
}

export const myr = (amount_minor: number): Money => ({ amount_minor, currency: "MYR" });

export function formatMoney(m: Money | number | null | undefined, locale = "en-MY"): string {
  if (m === null || m === undefined) return "";
  const minor = typeof m === "number" ? m : m.amount_minor;
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const whole = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, "0");
  return `${sign}RM ${new Intl.NumberFormat(locale).format(whole)}.${frac}`;
}

/** "1,500.50" -> 150050. Returns null for anything that is not a plain amount with at most two decimals. */
export function parseMoney(text: string): number | null {
  const t = text.replace(/[,\s]/g, "").replace(/^RM/i, "");
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(t);
  if (!m) return null;
  const whole = Number(m[1]);
  const frac = Number((m[2] ?? "").padEnd(2, "0") || "0");
  const minor = whole * 100 + frac;
  return Number.isSafeInteger(minor) ? minor : null;
}

/** Basis points are integers too: "1.5" (percent) -> 150. */
export function percentToBp(text: string): number | null {
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!m) return null;
  const bp = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0") || "0");
  return bp <= 10000 ? bp : null;
}

export const bpToPercent = (bp: number): string => `${Math.floor(bp / 100)}.${String(bp % 100).padStart(2, "0")}`;
