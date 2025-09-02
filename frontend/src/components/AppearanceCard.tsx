import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CHOICES, getTheme, setTheme, type ThemeChoice } from "../lib/theme";
import { Field, Select } from "./ui";

/** Light, dark or follow the device. Stored on this device only: it is a display preference, not account data. */
export function AppearanceCard() {
  const { t } = useTranslation();
  const [choice, setChoice] = useState<ThemeChoice>(getTheme);
  return (
    <section className="card" aria-labelledby="appearance-h">
      <h2 id="appearance-h" style={{ margin: 0 }}>{t("appearance.title")}</h2>
      <Field label={t("appearance.theme")}>
        <Select value={choice} onChange={(e) => { const v = e.target.value as ThemeChoice; setChoice(v); setTheme(v); }}>
          {CHOICES.map((c) => <option key={c} value={c}>{t(`appearance.${c}`)}</option>)}
        </Select>
      </Field>
      <p className="muted" style={{ margin: 0 }}>{t("appearance.help")}</p>
    </section>
  );
}
