/** Dates as people in Malaysia write them: 20-11-2027 (DD-MM-YYYY).
 *  The API sends calendar dates as YYYY-MM-DD and moments as ISO timestamps (UTC); a moment is shown in
 *  local time, so 2025-11-20T18:00:00Z reads 21-11-2025 in Kuala Lumpur. */

const pad = (n: number) => String(n).padStart(2, "0");
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function formatDate(value?: string | null): string {
  if (!value) return "";
  const m = DATE.exec(value);
  if (m) return `${m[3]}-${m[2]}-${m[1]}`; // a calendar date: no time zone involved
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  if (DATE.test(value) || Number.isNaN(d.getTime())) return formatDate(value);
  return `${formatDate(value)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A billing month, "2025-11" -> "11-2025", in the same order as dates. */
export function formatMonth(value?: string | null): string {
  const m = value ? /^(\d{4})-(\d{2})$/.exec(value) : null;
  return m ? `${m[2]}-${m[1]}` : (value ?? "");
}
