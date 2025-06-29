import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import ms from "./ms.json";

export const LANGUAGES = { en: "English", ms: "Bahasa Melayu" } as const;
export type Lang = keyof typeof LANGUAGES;

const saved = (typeof localStorage !== "undefined" && localStorage.getItem("lang")) || "en";

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, ms: { translation: ms } },
  lng: saved in LANGUAGES ? saved : "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

// screen readers pick their voice from <html lang>: right from the first page, not only after a switch
if (typeof document !== "undefined") document.documentElement.lang = i18n.language;

export function setLanguage(lang: Lang): void {
  localStorage.setItem("lang", lang);
  void i18n.changeLanguage(lang);
  document.documentElement.lang = lang;
}

export default i18n;
