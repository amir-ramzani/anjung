import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { api, unwrap } from "../api/client";
import { LanguageSwitch } from "../components/Layout";
import { Button, ErrorBox, Field, Input, Loading } from "../components/ui";
import { BrandMark, rememberHandle, useApplyBrand } from "../lib/brand";
import { formatDate } from "../lib/dates";
import { useAction } from "../lib/hooks";

/** Strong enough for the user pool: 12+ characters, upper and lower case, a number and a symbol. */
export const passwordProblems = (p: string): string[] => [
  ...(p.length < 12 ? ["length"] : []),
  ...(!/[a-z]/.test(p) || !/[A-Z]/.test(p) ? ["case"] : []),
  ...(!/[0-9]/.test(p) ? ["number"] : []),
  ...(!/[^A-Za-z0-9\s]/.test(p) ? ["symbol"] : []),
];

/** The set-password page a tenant (or, since contract v0.15.0, a staff member) opens from the welcome email, WhatsApp
 * or a shared link. No sign-in needed: the link is the key. */
export default function InvitePage() {
  const { t } = useTranslation();
  const { token = "" } = useParams();
  const info = useQuery({ queryKey: ["invite", token], retry: false, queryFn: () => unwrap(api.GET("/v1/invites/{token}", { params: { path: { token } } })) });
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [done, setDone] = useState(false);
  const accept = useAction(() => unwrap(api.POST("/v1/invites/{token}/accept", { params: { path: { token } }, body: { password: pw } })), [],
    () => { setDone(true); rememberHandle(info.data?.brand?.handle ?? null); });
  const brand = info.data?.brand ?? null;
  useApplyBrand(brand);
  const problems = passwordProblems(pw);
  const mismatch = again.length > 0 && again !== pw;
  const submit = (e: FormEvent) => { e.preventDefault(); if (!problems.length && !mismatch) accept.run(undefined as never); };

  let body;
  if (info.isLoading) body = <Loading />;
  else if (!info.data) body = <p role="alert" className="error">{t("invite.gone")}</p>;
  else if (done) body = (
    <>
      <p className="success" role="status">{t("invite.done", { email: info.data.email })}</p>
      <Link className="btn primary" to={info.data.brand ? `/login/${info.data.brand.handle}` : "/login"}>{t("login.signIn")}</Link>
    </>
  );
  else body = (
    <form onSubmit={submit}>
      <p>{t(info.data.kind === "staff" ? "invite.welcomeStaff" : "invite.welcome", { name: info.data.full_name, org: info.data.org_name })}</p>
      <p className="muted">{t("invite.signInWith", { email: info.data.email, date: formatDate(info.data.expires_at) })}</p>
      <ErrorBox error={accept.error} />
      <input type="email" hidden readOnly autoComplete="username" value={info.data.email} />
      <Field label={t("invite.password")} error={accept.fields["password"]}>
        <Input type="password" required autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
      </Field>
      <ul className="plain rules">
        {(["length", "case", "number", "symbol"] as const).map((r) => (
          <li key={r} className={pw && !problems.includes(r) ? "success" : "muted"}>{pw && !problems.includes(r) ? "✓" : "•"} {t(`invite.rules.${r}`)}</li>
        ))}
      </ul>
      <Field label={t("invite.confirm")} error={mismatch ? t("invite.mismatch") : undefined}>
        <Input type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
      </Field>
      <Button type="submit" disabled={accept.pending || problems.length > 0 || again !== pw}>{t("invite.setPassword")}</Button>
    </form>
  );
  return (
    <main className="login">
      <BrandMark brand={brand} className="login-brand" />
      <h2>{t("invite.pageTitle")}</h2>
      {body}
      <LanguageSwitch />
    </main>
  );
}
