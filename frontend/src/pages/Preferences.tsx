import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, unwrap, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { AppearanceCard } from "../components/AppearanceCard";
import { Button, ErrorBox, Field, Input, Loading, Page, Select } from "../components/ui";
import { useAction } from "../lib/hooks";

type Prefs = Schemas["NotificationPreferences"];

/** Opting in or out takes effect on the very next message (LLD 9.2). Opting out also records the withdrawal. */
export default function Preferences() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["prefs"], queryFn: () => unwrap(api.GET("/v1/notifications/preferences")) });
  const [f, setF] = useState({ email: false, whatsapp: false, number: "", locale: "en" });
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (q.data) setF({ email: !!q.data.email?.enabled, whatsapp: !!q.data.whatsapp?.enabled, number: q.data.whatsapp?.number ?? "", locale: q.data.locale ?? "en" });
  }, [q.data]);
  const save = useAction(
    () => unwrap(api.PUT("/v1/notifications/preferences", { body: {
      locale: f.locale as Prefs["locale"], email: { enabled: f.email },
      whatsapp: f.whatsapp ? { enabled: true, number: f.number } : { enabled: false, ...(f.number ? { number: f.number } : {}) },
    } })),
    [["prefs"]], () => setSaved(true),
  );
  // consent is a separate, explicit record (PDPA): ticking a channel here also records it
  const consent = useAction((purpose: "email_messaging" | "whatsapp_messaging") =>
    unwrap(api.POST("/v1/tenants/{tenant_id}/consents", { params: { path: { tenant_id: me?.tenant_id ?? "" } }, body: { purpose, policy_version: "2025-11", granted: true } })));
  if (q.isLoading) return <Loading />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    if (f.email) await consent.runAsync("email_messaging").catch(() => undefined);
    if (f.whatsapp) await consent.runAsync("whatsapp_messaging").catch(() => undefined);
    save.run(undefined as never);
  };
  return (
    <Page title={t("prefs.title")}>
      <AppearanceCard />
      <p className="muted">{t("prefs.help")}</p>
      <form onSubmit={(e) => void submit(e)} className="card">
        <ErrorBox error={save.error} />
        <label className="check"><input type="checkbox" checked={f.email} onChange={(e) => setF({ ...f, email: e.target.checked })} />{t("prefs.email")}</label>
        <label className="check"><input type="checkbox" checked={f.whatsapp} onChange={(e) => setF({ ...f, whatsapp: e.target.checked })} />{t("prefs.whatsapp")}</label>
        {f.whatsapp && (
          <Field label={t("prefs.number")} error={save.fields["whatsapp.number"]}>
            <Input required inputMode="tel" placeholder="+60123456789" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} />
          </Field>
        )}
        <Field label={t("prefs.language")}>
          <Select value={f.locale} onChange={(e) => setF({ ...f, locale: e.target.value })}><option value="en">English</option><option value="ms">Bahasa Melayu</option></Select>
        </Field>
        <Button type="submit" disabled={save.pending}>{t("common.save")}</Button>
        {saved && <p role="status" className="success">{t("common.saved")}</p>}
      </form>
    </Page>
  );
}
