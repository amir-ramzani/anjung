import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import en from "./en.json";
import ms from "./ms.json";

const flat = (o: Record<string, unknown>, prefix = ""): string[] =>
  Object.entries(o).flatMap(([k, v]) => (v && typeof v === "object" ? flat(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`]));

const files = (dir: string): string[] => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) && !/\.test\./.test(f) && !f.endsWith(".d.ts") ? [p] : [];
});

describe("translations", () => {
  it("English and Malay have exactly the same keys", () => {
    expect(flat(ms).sort()).toEqual(flat(en).sort());
  });
  it("has no empty strings", () => {
    for (const k of [...flat(en), ...flat(ms)]) expect(k).toBeTruthy();
    expect(Object.values(JSON.stringify(ms).match(/:""/g) ?? [])).toHaveLength(0);
  });
  it("has a label for every status the API can return", () => {
    // collects every `status` enum in the contract, so a new status without a label fails here
    const contract = JSON.parse(readFileSync("../contracts/openapi.json", "utf8")) as unknown;
    const statuses = new Set<string>();
    const walk = (o: unknown): void => {
      if (Array.isArray(o)) { o.forEach(walk); return; }
      if (!o || typeof o !== "object") return;
      for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
        const e = (v as { enum?: unknown[] } | null)?.enum;
        if (k === "status" && Array.isArray(e)) e.forEach((x) => typeof x === "string" && statuses.add(x));
        walk(v);
      }
    };
    walk(contract);
    expect(statuses.size).toBeGreaterThan(10);
    for (const lang of [en, ms]) expect([...statuses].filter((x) => !(x in (lang as { status: object }).status))).toEqual([]);
  });
  it("covers every literal key the code asks for", () => {
    const known = new Set(flat(en));
    const used = new Set<string>();
    for (const f of files("src")) for (const m of readFileSync(f, "utf8").matchAll(/\bt\("([a-zA-Z0-9_.]+)"/g)) used.add(m[1]!);
    const nav = readFileSync("src/components/Layout.tsx", "utf8");
    for (const m of nav.matchAll(/label: "([a-z.]+)"/g)) used.add(m[1]!);
    expect([...used].filter((k) => !known.has(k))).toEqual([]);
  });
  it("covers every dynamic key family", () => {
    const known = new Set(flat(en));
    for (const k of ["cash", "bank_transfer", "duitnow_transfer"]) expect(known).toContain(`invoices.methods.${k}`);
    for (const k of ["security", "utility", "access_card", "other"]) expect(known).toContain(`leases.depositTypes.${k}`);
    for (const k of ["plumbing", "electrical", "appliance", "structural", "pest", "other"]) expect(known).toContain(`maintenance.categories.${k}`);
    for (const k of ["triaged", "assigned", "in_progress", "resolved", "closed", "cancelled"]) expect(known).toContain(`maintenance.moveTo.${k}`);
    for (const k of ["open", "partially_paid", "paid", "overdue", "void", "draft", "active", "terminated", "expired"]) expect(known).toContain(`status.${k}`);
    for (const k of ["org_admin", "property_manager"]) expect(known).toContain(`users.roles.${k}`);
  });
});
