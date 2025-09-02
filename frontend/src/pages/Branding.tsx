import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Uploader } from "../components/Documents";
import { Button, ErrorBox, Field, Input } from "../components/ui";
import { config } from "../config";
import { readableOnWhite } from "../lib/brand";
import { useAction } from "../lib/hooks";

type BrandingIn = Schemas["BrandingIn"];
const EMPTY = {
  enabled: false, handle: "", display_name: "", company_name: "", company_reg_no: "", accent_colour: "",
  contact_email: "", contact_phone: "", welcome_en: "", welcome_ms: "", login_page: true,
};
export type BrandForm = typeof EMPTY;

/** The form's values from an organisation's branding (empty when it has none). */
export function brandForm(b: Schemas["Branding"] | null | undefined): BrandForm {
  return {
    enabled: !!b?.enabled, handle: b?.handle ?? "", display_name: b?.display_name ?? "", company_name: b?.company_name ?? "",
    company_reg_no: b?.company_reg_no ?? "", accent_colour: b?.accent_colour ?? "", contact_email: b?.contact_email ?? "",
    contact_phone: b?.contact_phone ?? "", welcome_en: b?.welcome_text?.en ?? "", welcome_ms: b?.welcome_text?.ms ?? "",
    login_page: b?.login_page !== false,
  };
}

const blank = (v: string) => (v.trim() ? v.trim() : null);

/** What the API takes: a blank field clears it. The logo is set separately, by the org admin. */
export function brandBody(f: BrandForm): BrandingIn {
  return {
    enabled: f.enabled, handle: blank(f.handle.toLowerCase()), display_name: blank(f.display_name), company_name: blank(f.company_name),
    company_reg_no: blank(f.company_reg_no), accent_colour: blank(f.accent_colour), contact_email: blank(f.contact_email),
    contact_phone: blank(f.contact_phone), login_page: f.login_page,
    welcome_text: { en: blank(f.welcome_en), ms: blank(f.welcome_ms) },
  };
}

/** The branding fields, shared by the org admin's Settings and the platform admin's company details (v0.17.0). */
export function BrandingFields({ f, onChange, errors, orgName }: {
  f: BrandForm; onChange: (f: BrandForm) => void; errors: Record<string, string>; orgName?: string;
}) {
  const { t } = useTranslation();
  const set = (k: keyof BrandForm) => (e: { target: { value: string } }) => onChange({ ...f, [k]: e.target.value });
  const colourOk = !f.accent_colour || readableOnWhite(f.accent_colour);
  const page = f.handle ? `${window.location.origin}/login/${f.handle.toLowerCase()}` : null;
  return (
    <>
      <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => onChange({ ...f, enabled: e.target.checked })} />{t("branding.enabled")}</label>
      <Field label={t("branding.handle")} error={errors["branding.handle"]}>
        <Input value={f.handle} onChange={(e) => onChange({ ...f, handle: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} pattern="[a-z0-9][a-z0-9-]{1,28}[a-z0-9]" placeholder="rumah-kita" required={f.enabled} />
      </Field>
      {page && <small className="muted">{t("branding.pageAt")} <code>{page}</code></small>}
      <Field label={t("branding.displayName")}><Input maxLength={100} value={f.display_name} onChange={set("display_name")} placeholder={orgName} /></Field>
      <Field label={t("branding.companyName")}><Input maxLength={200} value={f.company_name} onChange={set("company_name")} /></Field>
      <Field label={t("branding.regNo")}><Input maxLength={50} value={f.company_reg_no} onChange={set("company_reg_no")} placeholder="202301012345 (1234567-X)" /></Field>
      <Field label={t("branding.colour")} error={colourOk ? errors["branding.accent_colour"] : t("branding.colourContrast")}>
        <Input type="color" value={f.accent_colour || "#0f766e"} onChange={set("accent_colour")} />
      </Field>
      <Field label={t("branding.contactEmail")} error={errors["branding.contact_email"]}><Input type="email" value={f.contact_email} onChange={set("contact_email")} /></Field>
      <Field label={t("branding.contactPhone")} error={errors["branding.contact_phone"]}><Input inputMode="tel" placeholder="+60312345678" value={f.contact_phone} onChange={set("contact_phone")} /></Field>
      <Field label={t("branding.welcomeEn")}><Input maxLength={300} value={f.welcome_en} onChange={set("welcome_en")} /></Field>
      <Field label={t("branding.welcomeMs")}><Input maxLength={300} value={f.welcome_ms} onChange={set("welcome_ms")} /></Field>
      <label className="check"><input type="checkbox" checked={f.login_page} onChange={(e) => onChange({ ...f, login_page: e.target.checked })} />{t("branding.loginPage")}</label>
    </>
  );
}

/** Settings → Branding (contract v0.11.0, org admin): the company's own name, logo and sign-in page. */
export function BrandingCard() {
  const { t } = useTranslation();
  const { me, reload } = useAuth();
  const org = useQuery({ queryKey: ["org"], queryFn: () => unwrap(api.GET("/v1/org")) });
  const b = org.data?.branding;
  const [f, setF] = useState(EMPTY);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (org.data) setF(brandForm(b));
  }, [org.data, b]);
  const keys = [["org"], ["me"]];
  const patch = (branding: BrandingIn) => unwrap(api.PATCH("/v1/org", { body: { branding } }));
  const save = useAction(() => patch(brandBody(f)), keys, () => { setSaved(true); reload(); });
  const setLogo = useAction((id: string | null) => patch({ logo_document_id: id }), keys, reload);
  const submit = (e: FormEvent) => { e.preventDefault(); save.run(undefined as never); };
  return (
    <section className="card" aria-labelledby="branding-h">
      <h2 id="branding-h" style={{ margin: 0 }}>{t("branding.title")}</h2>
      <p className="muted" style={{ margin: 0 }}>{t("branding.help")}</p>
      <form onSubmit={submit} className="form-stack">
        <ErrorBox error={save.error} />
        <BrandingFields f={f} onChange={(v) => { setSaved(false); setF(v); }} errors={save.fields} orgName={org.data?.name} />
        <Button type="submit" disabled={save.pending}>{t("branding.save")}</Button>
        {saved && <p role="status" className="success">{t("common.saved")}</p>}
      </form>
      <h3>{t("branding.logo")}</h3>
      {b?.logo_url
        ? <p><img className="brand-logo preview" src={`${config.apiBaseUrl}${b.logo_url}`} alt={t("branding.logo")} /> <Button variant="secondary" onClick={() => setLogo.run(null)}>{t("common.remove")}</Button></p>
        : <p className="muted">{b?.handle ? t("branding.noLogo") : t("branding.logoNeedsHandle")}</p>}
      <ErrorBox error={setLogo.error} />
      {b?.handle && me && <Uploader ownerType="organization" ownerId={me.org_id} types={["logo"]} onClean={(id) => setLogo.run(id)} />}
      <small className="muted">{t("branding.logoLimits")}</small>
    </section>
  );
}
