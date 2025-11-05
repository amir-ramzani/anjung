#!/usr/bin/env node
/**
 * Builds the Postman collection from ../openapi.json (LLD 16.3). One folder per tag, the hand-written Flows folder,
 * OAuth 2.0 with PKCE against the Cognito hosted UI, and an Idempotency-Key pre-request script.
 *
 *   node postman/build.mjs           write postman/anjung.postman_collection.json
 *   node postman/build.mjs --check   fail if the committed file differs (CI): the collection never lags the contract
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

import { flows } from "./flows.mjs";

const require = createRequire(import.meta.url);
const Converter = require("openapi-to-postmanv2");
const here = path.dirname(fileURLToPath(import.meta.url));
const spec = fs.readFileSync(path.join(here, "..", "openapi.json"), "utf8");
const target = path.join(here, "anjung.postman_collection.json");
const api = JSON.parse(spec);
const version = api.info.version;

const convert = () => new Promise((resolve, reject) =>
  Converter.convert({ type: "string", data: spec }, {
    folderStrategy: "Tags", requestParametersResolution: "Example", exampleParametersResolution: "Example",
    includeAuthInfoInExample: false, optimizeConversion: false, stackLimit: 50,
  }, (err, r) => (err ? reject(err) : r.result ? resolve(r.output[0].data) : reject(new Error(r.reason)))));

/** Random ids would make every build differ: drop them, Postman makes its own on import. */
const stripIds = (o) => {
  if (Array.isArray(o)) return o.map(stripIds);
  if (o && typeof o === "object") {
    return Object.fromEntries(Object.entries(o).filter(([k]) => !["id", "_postman_id", "uid"].includes(k)).map(([k, v]) => [k, stripIds(v)]));
  }
  return o;
};

const oauth = {
  type: "oauth2",
  oauth2: [
    ["grant_type", "authorization_code_with_pkce"], ["authUrl", "{{cognito_domain}}/oauth2/authorize"],
    ["accessTokenUrl", "{{cognito_domain}}/oauth2/token"], ["clientId", "{{client_id}}"],
    ["redirect_uri", "https://oauth.pstmn.io/v1/callback"], ["scope", "openid email"], ["challengeAlgorithm", "S256"],
    ["addTokenTo", "header"], ["tokenName", "Anjung"], ["client_authentication", "body"],
  ].map(([key, value]) => ({ key, value, type: "string" })),
};

const idempotencyScript = [
  "// requests that need an Idempotency-Key get a fresh one each time they are sent",
  'if (pm.request.headers.has("Idempotency-Key")) {',
  '  pm.request.headers.upsert({ key: "Idempotency-Key", value: pm.variables.replaceIn("{{$guid}}") });',
  "}",
];

// ---- deterministic examples from the contract's own schemas (the converter's are random)
const deref = (node) => (node?.$ref ? deref(node.$ref.slice(2).split("/").reduce((o, k) => o[k], api)) : node);
const PATTERNS = [[/\[0-9A-HJKMNP-TV-Z\]\{26\}/, "01JD8Z3K9Q4M7T2V6X8B5N1C0A"], [/\(0\[1-9\]\|1\[0-2\]\)/, "2018-12"], [/\\\+\[1-9\]/, "+60123456789"], [/\[A-Z\]\{2\}/, "MY"], [/\[0-9\]\{5\}/, "50000"]];
function example(schema, depth = 0) {
  const s = deref(schema);
  if (!s || depth > 6) return null;
  if ("example" in s) return s.example;
  if (s.examples?.length) return s.examples[0];
  if ("const" in s) return s.const;
  if ("default" in s) return s.default;
  if (s.enum) return s.enum.find((v) => v !== null);
  if (s.oneOf || s.anyOf) return example((s.oneOf ?? s.anyOf).find((o) => deref(o)?.type !== "null"), depth + 1);
  if (s.allOf) return Object.assign({}, ...s.allOf.map((o) => example(o, depth + 1)));
  const type = Array.isArray(s.type) ? s.type.find((t) => t !== "null") : s.type;
  if (type === "object" || s.properties) {
    return Object.fromEntries((s.required ?? []).filter((k) => s.properties?.[k]).map((k) => [k, example(s.properties[k], depth + 1)]));
  }
  if (type === "array") return Array.from({ length: s.minItems ?? 1 }, () => example(s.items, depth + 1));
  if (type === "integer" || type === "number") return s.minimum ?? 1;
  if (type === "boolean") return false;
  if (s.format === "date") return "2018-12-01";
  if (s.format === "date-time") return "2018-12-01T00:00:00Z";
  if (s.format === "email") return "user@example.com";
  if (s.format === "uri") return "https://example.com";
  if (s.pattern) for (const [re, v] of PATTERNS) if (re.test(s.pattern)) return v;
  return "string";
}
const paramList = (op, item) => [...(item.parameters ?? []), ...(op.parameters ?? [])].map((p) => deref(p));

/** Rebuilds URL, query, headers and body of every generated request from the contract, so the file is reproducible. */
function rebuild(items) {
  const ops = new Map();
  for (const [route, item] of Object.entries(api.paths)) {
    for (const [method, op] of Object.entries(item)) if (op.operationId) ops.set(`${method.toUpperCase()} ${route}`, { op, item });
  }
  const walk = (list) => {
    for (const it of list) {
      if (it.item) { walk(it.item); continue; }
      const req = it.request;
      if (!req) continue;
      const given = typeof req.url === "string" ? req.url : (req.url.raw ?? `/${req.url.path.join("/")}`);
      const raw = given.split("?")[0].replace("{{baseUrl}}", "").replace(/:([A-Za-z_]+)/g, "{$1}");
      const found = ops.get(`${req.method} ${raw}`);
      if (!found) continue; // a hand-written request
      const { op, item: pathItem } = found;
      const params = paramList(op, pathItem);
      const pathTemplate = raw.replace(/\{([^}]+)\}/g, ":$1");
      req.url = {
        raw: `{{base_url}}${pathTemplate}`, host: ["{{base_url}}"], path: pathTemplate.split("/").filter(Boolean),
        variable: params.filter((p) => p.in === "path").map((p) => ({ key: p.name, value: String(example(p.schema)), description: p.description ?? "" })),
        query: params.filter((p) => p.in === "query").map((p) => ({ key: p.name, value: String(example(p.schema) ?? ""), disabled: !p.required })),
      };
      if (!req.url.variable.length) delete req.url.variable;
      if (!req.url.query.length) delete req.url.query;
      req.header = params.filter((p) => p.in === "header").map((p) => ({ key: p.name, value: p.name === "Idempotency-Key" ? "{{$guid}}" : String(example(p.schema)) }));
      const body = op.requestBody?.content?.["application/json"]?.schema;
      if (body) {
        req.header.push({ key: "Content-Type", value: "application/json" });
        req.body = { mode: "raw", raw: JSON.stringify(example(body), null, 2), options: { raw: { language: "json" } } };
      } else delete req.body;
    }
  };
  walk(items);
}

function fixRequests(items) {
  for (const it of items) {
    if (it.item) { fixRequests(it.item); continue; }
    if (!it.request) continue;
    delete it.response; // saved example responses are invented, not useful, and noisy in review
    const webhook = JSON.stringify(it.request.url).includes("webhooks");
    if (webhook) it.request.auth = { type: "noauth" };
    else delete it.request.auth; // inherit the collection's OAuth 2.0
    for (const h of it.request.header ?? []) if (h.key === "Idempotency-Key") h.value = "{{$guid}}";
  }
}

const sorted = (items) => [...items].sort((a, b) => a.name.localeCompare(b.name));

const collection = stripIds(await convert());
collection.info = {
  name: "Anjung API",
  description: `Generated from contracts/openapi.json v${version}. Do not edit by hand: change the contract and run \`npm run postman\`. The Flows folder is hand-written in postman/flows.mjs.`,
  schema: "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
};
collection.item = [...sorted(collection.item.filter((i) => i.name !== "Flows")), stripIds(flows)];
rebuild(collection.item);
fixRequests(collection.item);
collection.auth = oauth;
collection.event = [{ listen: "prerequest", script: { type: "text/javascript", exec: idempotencyScript } }];
collection.variable = [
  { key: "base_url", value: "https://api.dev.anjung.my", type: "string" },
  { key: "cognito_domain", value: "", type: "string" },
  { key: "client_id", value: "", type: "string" },
];
const out = JSON.stringify(JSON.parse(JSON.stringify(collection).replaceAll("{{baseUrl}}", "{{base_url}}")), null, 2) + "\n";

if (process.argv.includes("--check")) {
  const committed = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  if (committed !== out) {
    console.error("The Postman collection is out of date with contracts/openapi.json. Run: npm run postman (in contracts/)");
    process.exit(1);
  }
  console.log(`Postman collection is current (contract v${version}).`);
} else {
  fs.writeFileSync(target, out);
  const ops = (items) => items.flatMap((i) => (i.item ? ops(i.item) : [i]));
  console.log(`wrote ${path.relative(process.cwd(), target)}: ${ops(collection.item).length} requests (contract v${version})`);
}
