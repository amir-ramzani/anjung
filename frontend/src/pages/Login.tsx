import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import * as cognito from "../auth/cognito";
import { LanguageSwitch } from "../components/Layout";
import { InstallApp } from "../lib/pwa";
import { Button, Field, Input } from "../components/ui";
import { config } from "../config";
import { BrandMark, rememberHandle, rememberedHandle, usePublicBrand, useApplyBrand, type Brand } from "../lib/brand";
import { passwordProblems } from "./Invite";

/** Sign-in. A plain `/login` is always the Anjung page; `/login/<handle>` is a company's own branded page
 * (contract v0.11.0). The browser remembers the company, so sign-out and an expired session go back to its page. */
export default function Login() {
  const { handle: fromRoute } = useParams();
  const { status, error } = useAuth();
  const brandQ = usePublicBrand(fromRoute);
  const brand = fromRoute && brandQ.data && brandQ.data.login_page !== false ? brandQ.data : null;
  useEffect(() => {
    if (brandQ.isSuccess && fromRoute) rememberHandle(fromRoute);
    if (brandQ.isError && fromRoute && rememberedHandle() === fromRoute) rememberHandle(null); // gone or switched off
  }, [brandQ.isSuccess, brandQ.isError, fromRoute]);
  useApplyBrand(brand);
  if (status === "authenticated") return <Navigate to="/" replace />;

  return (
    <main className="login">
      <BrandMark brand={brand} className="login-brand" />
      <Welcome brand={brand} />
      {error && <div role="alert" className="error">{error}</div>}
      {config.authMode === "mock" ? <MockSignIn /> : <CognitoSignIn />}
      <LanguageSwitch />
      <InstallApp />
      {brand && <CompanyFooter brand={brand} />}
    </main>
  );
}

function Welcome({ brand }: { brand: Brand | null }) {
  const { t, i18n } = useTranslation();
  if (!brand) return <p className="muted">{t("login.tagline")}</p>;
  const lang = i18n.language === "ms" ? "ms" : "en";
  const text = brand.welcome_text?.[lang] ?? brand.welcome_text?.en ?? t("login.brandTagline", { name: brand.display_name });
  return <p>{text}</p>;
}

function CompanyFooter({ brand }: { brand: Brand }) {
  const { t } = useTranslation();
  const company = brand.company_name ?? brand.display_name;
  return (
    <footer className="muted login-footer">
      <span>{company}{brand.company_reg_no ? ` (${brand.company_reg_no})` : ""}</span>
      {(brand.contact_phone || brand.contact_email) && <span>{[brand.contact_phone, brand.contact_email].filter(Boolean).join(" · ")}</span>}
      <span>{t("login.poweredBy")}</span>
    </footer>
  );
}

function MockSignIn() {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await signIn(email); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit}>
      <p className="notice">{t("login.mockNotice")}</p>
      <Field label={t("login.email")}>
        <Input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Button type="submit" disabled={busy}>{t("login.signIn")}</Button>
    </form>
  );
}

function CognitoSignIn() {
  const { t } = useTranslation();
  const { signIn, challenge, answer } = useAuth();
  const [mode, setMode] = useState<"sign_in" | "forgot">("sign_in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const run = (fn: () => Promise<void>) => async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  if (challenge === "new_password") {
    const problems = passwordProblems(value);
    return (
      <form onSubmit={run(() => answer(value))}>
        <p>{t("login.newPasswordHelp")}</p>
        <Field label={t("invite.password")}><Input type="password" required autoComplete="new-password" value={value} onChange={(e) => setValue(e.target.value)} /></Field>
        <Rules problems={problems} typed={!!value} />
        <Button type="submit" disabled={busy || problems.length > 0}>{t("invite.setPassword")}</Button>
      </form>
    );
  }
  if (challenge === "mfa") {
    return (
      <form onSubmit={run(() => answer(value))}>
        <Field label={t("login.mfaCode")}><Input required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" value={value} onChange={(e) => setValue(e.target.value)} /></Field>
        <Button type="submit" disabled={busy}>{t("login.verify")}</Button>
      </form>
    );
  }
  if (mode === "forgot") return <ForgotPassword initialEmail={email} onDone={() => setMode("sign_in")} />;
  return (
    <form onSubmit={run(() => signIn(email, password))}>
      <Field label={t("login.email")}><Input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label={t("login.password")}><Input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      <Button type="submit" disabled={busy}>{t("login.signIn")}</Button>
      <button type="button" className="link" onClick={() => setMode("forgot")}>{t("login.forgot")}</button>
    </form>
  );
}

function Rules({ problems, typed }: { problems: string[]; typed: boolean }) {
  const { t } = useTranslation();
  return (
    <ul className="plain rules">
      {(["length", "case", "number", "symbol"] as const).map((r) => (
        <li key={r} className={typed && !problems.includes(r) ? "success" : "muted"}>{typed && !problems.includes(r) ? "✓" : "•"} {t(`invite.rules.${r}`)}</li>
      ))}
    </ul>
  );
}

function ForgotPassword({ initialEmail, onDone }: { initialEmail: string; onDone: () => void }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<"email" | "code" | "done">("email");
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const attempt = (fn: () => Promise<void>, next: typeof step) => async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try { await fn(); setStep(next); } catch (x) { setErr(t(cognito.errorKey(x))); } finally { setBusy(false); }
  };
  if (step === "done") return (<><p className="success" role="status">{t("login.passwordChanged")}</p><Button onClick={onDone}>{t("login.signIn")}</Button></>);
  const problems = passwordProblems(password);
  return (
    <form onSubmit={step === "email" ? attempt(() => cognito.forgotPassword(email), "code") : attempt(() => cognito.confirmNewPassword(email, code, password), "done")}>
      {err && <div role="alert" className="error">{err}</div>}
      <Field label={t("login.email")}><Input type="email" required autoComplete="username" readOnly={step === "code"} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      {step === "code" && (<>
        <p className="muted">{t("login.codeSent")}</p>
        <Field label={t("login.code")}><Input required inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <Field label={t("invite.password")}><Input type="password" required autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <Rules problems={problems} typed={!!password} />
      </>)}
      <div className="actions">
        <Button type="submit" disabled={busy || (step === "code" && problems.length > 0)}>{t(step === "email" ? "login.sendCode" : "invite.setPassword")}</Button>
        <Button type="button" variant="secondary" onClick={onDone}>{t("common.cancel")}</Button>
      </div>
    </form>
  );
}
