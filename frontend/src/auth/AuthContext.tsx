import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError, api, bindSession, unwrap, type Schemas } from "../api/client";
import { config } from "../config";
import i18n from "../i18n";
import * as cognito from "./cognito";
import type { Tokens } from "./tokens";

export type Me = Schemas["Me"];
export type Role = Schemas["Role"];

interface AuthValue {
  status: "loading" | "anonymous" | "authenticated";
  me: Me | null;
  error: string | null;
  /** mock mode: sign in with an email. Cognito mode: email and password on our own page (SRP), which may
   * then ask for a new password (invited by email) or an MFA code: see `challenge` and `answer`. */
  signIn: (email?: string, password?: string) => Promise<void>;
  challenge: "new_password" | "mfa" | null;
  answer: (value: string) => Promise<void>;
  signOut: () => void;
  has: (...roles: Role[]) => boolean;
  /** Read /v1/me again (after the organisation's branding changed). */
  reload: () => void;
}

const KEY = "anjung.tokens";
const Ctx = createContext<AuthValue | null>(null);

const load = (): Tokens | null => {
  try {
    const raw = sessionStorage.getItem(KEY); // cleared when the tab closes
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokens, setTokens] = useState<Tokens | null>(load);
  const tokensRef = useRef(tokens);
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<AuthValue["status"]>(tokens ? "loading" : "anonymous");
  const [error, setError] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<AuthValue["challenge"]>(null);
  const [meVersion, setMeVersion] = useState(0);
  const reload = useCallback(() => setMeVersion((v) => v + 1), []);

  const store = useCallback((t: Tokens | null) => {
    tokensRef.current = t;
    setTokens(t);
    if (t) sessionStorage.setItem(KEY, JSON.stringify(t));
    else sessionStorage.removeItem(KEY);
  }, []);

  const signOut = useCallback(() => {
    const t = tokensRef.current;
    if (config.authMode === "cognito" && t?.refreshToken) void cognito.revoke(t.refreshToken);
    store(null);
    setMe(null);
    setChallenge(null);
    setStatus("anonymous"); // the sign-in page then shows this company's branding again (remembered handle)
  }, [store]);

  const refresh = useCallback(async (): Promise<string | null> => {
    const t = tokensRef.current;
    if (config.authMode !== "cognito" || !t?.refreshToken) return null;
    try {
      const fresh = await cognito.refresh(t.refreshToken);
      store({ ...fresh, refreshToken: fresh.refreshToken ?? t.refreshToken });
      return fresh.accessToken;
    } catch {
      return null;
    }
  }, [store]);

  useEffect(() => {
    bindSession({
      getToken: () => tokensRef.current?.accessToken ?? null, refresh, onSignedOut: signOut,
      // access ended while signed in (deactivated or past its dates): back to sign-in, with the reason
      onInactive: (message, code) => {
        setError(code === "plan_ended" ? i18n.t("login.denied.plan_ended") : message);
        store(null); setMe(null); setStatus("anonymous");
      },
    });
  }, [refresh, signOut, store]);

  // silent refresh a minute before the access token expires
  useEffect(() => {
    if (!tokens?.refreshToken) return;
    const wait = Math.max(tokens.expiresAt - Date.now() - 60_000, 5_000);
    const id = setTimeout(() => void refresh(), wait);
    return () => clearTimeout(id);
  }, [tokens, refresh]);

  // whenever there is a token, find out who it belongs to
  useEffect(() => {
    if (!tokens) return;
    let cancelled = false;
    unwrap(api.GET("/v1/me"))
      .then((m) => {
        if (!cancelled) {
          setMe(m);
          setStatus("authenticated");
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // 403: signed in but not allowed (suspended organisation, no membership). Show it, do not loop.
        setError(e instanceof ApiError ? e.message : "Could not load your account");
        store(null);
        setStatus("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, [tokens, store, meVersion]);

  const finish = useCallback((step: cognito.Step) => {
    if (step.kind === "done") {
      setChallenge(null);
      setStatus("loading");
      store(step.tokens);
    } else {
      setChallenge(step.kind);
    }
  }, [store]);

  const signIn = useCallback(
    async (email?: string, password?: string) => {
      setError(null);
      if (config.authMode === "mock") {
        const r = await fetch(`${config.apiBaseUrl}/dev/token`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }),
        });
        if (!r.ok) {
          const b = (await r.json().catch(() => ({}))) as { code?: string; message?: string; details?: { issue?: string }[] };
          const reason = b.code === "sign_in_denied" ? b.details?.[0]?.issue : undefined;
          const key = `login.denied.${reason}`;
          setError(reason && i18n.exists(key) ? i18n.t(key) : b.message ?? i18n.t("login.failed"));
          return;
        }
        const j = (await r.json()) as { access_token: string };
        setStatus("loading");
        store({ accessToken: j.access_token, refreshToken: null, expiresAt: Date.now() + 8 * 3600_000 });
        return;
      }
      try {
        finish(await cognito.signIn(email ?? "", password ?? ""));
      } catch (e) {
        setError(i18n.t(cognito.errorKey(e)));
      }
    },
    [store, finish],
  );

  const answer = useCallback(async (value: string) => {
    setError(null);
    try {
      finish(challenge === "mfa" ? await cognito.sendMfaCode(value) : await cognito.setNewPassword(value));
    } catch (e) {
      setError(i18n.t(cognito.errorKey(e)));
    }
  }, [challenge, finish]);

  const value = useMemo<AuthValue>(
    () => ({ status, me, error, signIn, challenge, answer, signOut, reload, has: (...roles) => !!me && roles.some((r) => me.roles.includes(r)) }),
    [status, me, error, signIn, challenge, answer, signOut, reload],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export const STAFF: Role[] = ["org_admin", "property_manager"];
