export type ThemeChoice = "system" | "light" | "dark";
const KEY = "anjung.theme";
export const CHOICES: ThemeChoice[] = ["system", "light", "dark"];

export function getTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system"; // storage can be blocked: fall back to following the device
  }
}

/** "system" leaves the attribute off so the stylesheet follows prefers-color-scheme. */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

export function setTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* the choice still applies for this visit */
  }
  applyTheme(choice);
}
