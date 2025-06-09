import createClient, { type Middleware } from "openapi-fetch";

import { config } from "../config";
import { issueText } from "../lib/errors";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type ApiErrorBody = Schemas["Error"];

/** The contract's error envelope as an exception. `code` is stable and machine readable. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: { field: string; issue: string; code?: string; params?: Record<string, string | number> }[] = [],
    public requestId = "",
    /** the values in the message, for showing it in the user's language (contract v0.13.1) */
    public params: Record<string, string | number> = {},
  ) {
    super(message);
  }
  /** field -> message in the user's language (contract v0.16.0), for showing validation errors next to inputs */
  get fieldErrors(): Record<string, string> {
    return Object.fromEntries(this.details.map((d) => [d.field, issueText(d)]));
  }
}

interface Session {
  getToken: () => string | null;
  /** called on a 401: try to get a fresh token, return it, or null to give up */
  refresh: () => Promise<string | null>;
  onSignedOut: () => void;
  /** called on 403 account_inactive: an admin disabled this staff account or its access dates ran out */
  onInactive?: (message: string, code: string) => void;
}

let session: Session | null = null;
export const bindSession = (s: Session) => {
  session = s;
};

const auth: Middleware = {
  onRequest({ request }) {
    const t = session?.getToken();
    if (t) request.headers.set("Authorization", `Bearer ${t}`);
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status === 403 && session?.onInactive) {
      const body = (await response.clone().json().catch(() => null)) as { code?: string; message?: string } | null;
      // the account, or the whole organisation's plan (contract v0.12.0), no longer allows signing in
      if (body?.code === "account_inactive" || body?.code === "plan_ended") session.onInactive(body.message ?? "Your access is not active", body.code);
      return response;
    }
    if (response.status !== 401 || request.headers.get("x-retried")) return response;
    const fresh = await session?.refresh();
    if (!fresh) {
      session?.onSignedOut();
      return response;
    }
    const retry = new Request(request, { headers: new Headers(request.headers) });
    retry.headers.set("Authorization", `Bearer ${fresh}`);
    retry.headers.set("x-retried", "1");
    return fetch(retry);
  },
};

// fetch is looked up on every call, not captured at start-up, so it can be replaced (tests, service workers)
// JSON types only: the CSV report downloads (contract v0.15.0) are read as blobs where they are asked for
export const api = createClient<paths, "application/json">({ baseUrl: config.apiBaseUrl, fetch: (req) => globalThis.fetch(req) });
api.use(auth);

/** Turns an openapi-fetch result into data or a thrown ApiError. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || !response.ok) {
    const e = (error ?? {}) as Partial<ApiErrorBody>;
    throw new ApiError(response.status, e.code ?? "unknown", e.message ?? response.statusText, e.details ?? [], e.request_id ?? "", e.params ?? {});
  }
  return data as T;
}

/** A fresh Idempotency-Key for each user action, reused if the same action is retried. */
export const newIdempotencyKey = (): string => crypto.randomUUID();
