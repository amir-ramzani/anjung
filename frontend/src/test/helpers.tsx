import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import "../i18n";

type Reply = unknown | ((req: Request, body: unknown) => unknown);
export interface Call { method: string; path: string; headers: Headers; body: unknown }

/** A fake API. Keys are "METHOD /path". A reply is a JSON body, { status, body }, or a ready Response. Unknown routes
 * fail the test. */
export function mockApi(routes: Record<string, Reply>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(input, init);
    const url = new URL(req.url, "http://localhost");
    const text = req.method === "GET" || req.method === "HEAD" ? "" : await req.clone().text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method: req.method, path: url.pathname, headers: req.headers, body });
    const key = `${req.method} ${url.pathname}`;
    const reply = routes[key];
    if (reply === undefined) throw new Error(`unexpected request ${key}`);
    const out = typeof reply === "function" ? (reply as (r: Request, b: unknown) => unknown)(req, body) : reply;
    if (out instanceof Response) return out; // e.g. a CSV download
    // { status: 422, body } is an HTTP error. A resource that merely has a `status` field (an invoice) is a body.
    const wrapped = (o: unknown): o is { status: number; body: unknown } =>
      !!o && typeof o === "object" && typeof (o as { status?: unknown }).status === "number" && "body" in (o as object);
    const { status, body: payload } = wrapped(out) ? out : { status: 200, body: out };
    return new Response(status === 204 ? null : JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn, count: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key).length };
}

export const ME = (roles: string[], extra: object = {}) => ({ user_id: "u1", org_id: "o1", roles, email: "me@example.com", tenant_id: null, locale: "en", ...extra });
export const money = (n: number) => ({ amount_minor: n, currency: "MYR" });
export const page = <T,>(items: T[]) => ({ items, next_cursor: null });

export function signedIn() {
  sessionStorage.setItem("anjung.tokens", JSON.stringify({ accessToken: "tok", refreshToken: null, expiresAt: Date.now() + 3600_000 }));
}

export function renderApp(route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider><App /></AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
