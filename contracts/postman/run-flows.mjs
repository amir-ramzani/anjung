#!/usr/bin/env node
/**
 * Runs the Flows folder with Newman against a LOCAL stack (`docker compose up`, mock auth). Newman cannot do the
 * browser PKCE flow, so for this run the OAuth block is swapped for a bearer token from /dev/token. It checks that the
 * hand-written flows still work against the real API, it is not how testers use the collection.
 *
 *   node postman/run-flows.mjs [--base-url http://localhost:8080] [--email root-admin-of-an-org@example.com]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const newman = require("newman");
const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : fallback; };
const base = arg("base-url", "http://localhost:8080");
const root = arg("platform-admin", "root@example.com");

async function token(email) {
  const r = await fetch(`${base}/dev/token`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }) });
  if (!r.ok) throw new Error(`/dev/token ${r.status}: ${await r.text()}`);
  return (await r.json()).access_token;
}

// a fresh organisation, so the run is repeatable and leaves nothing behind that matters
const stamp = Date.now();
const adminEmail = `postman-${stamp}@example.com`;
const org = await fetch(`${base}/v1/platform/organizations`, {
  method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${await token(root)}` },
  body: JSON.stringify({ name: `Flows ${stamp}`, initial_admin: { email: adminEmail }, reason: "postman flows" }),
});
if (org.status !== 201) throw new Error(`could not create an organisation: ${org.status} ${await org.text()}`);

const collection = JSON.parse(fs.readFileSync(path.join(here, "anjung.postman_collection.json"), "utf8"));
collection.auth = { type: "bearer", bearer: [{ key: "token", value: "{{access_token}}", type: "string" }] };

newman.run({
  collection, folder: "Flows", bail: false,
  envVar: [
    { key: "base_url", value: base }, { key: "access_token", value: await token(adminEmail) },
    { key: "fake_webhook_secret", value: process.env.FAKE_WEBHOOK_SECRET ?? "local-fake-webhook-secret" },
  ],
  reporters: "cli",
}, (err, summary) => {
  if (err) throw err;
  const failed = summary.run.failures.length;
  console.log(`\n${summary.run.stats.assertions.total} assertions, ${failed} failed`);
  process.exit(failed ? 1 : 0);
});
