import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/** Installing the app on a phone or desktop (P4-F4). The service worker is `public/sw.js`. */

export function registerServiceWorker(): void {
  // production builds only (Vite's dev server would be cached otherwise), and only where browsers allow it
  if (!import.meta.env.PROD || !("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined); // the app works without it
  });
}

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

const standalone = () =>
  window.matchMedia?.("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
const iosSafari = () => /iphone|ipad|ipod/i.test(navigator.userAgent) && !/crios|fxios|edgios/i.test(navigator.userAgent);

/** Chrome, Edge and Android offer an install prompt the app can show from its own button. Safari on iPhone has
 * none: the person adds it from the Share menu, so we say how. Nothing shows once the app is installed. */
export function InstallApp() {
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState<InstallEvent | null>(null);
  const [installed, setInstalled] = useState(standalone());
  const [hint, setHint] = useState(false);
  useEffect(() => {
    const offer = (e: Event) => { e.preventDefault(); setPrompt(e as InstallEvent); };
    const done = () => { setInstalled(true); setPrompt(null); };
    window.addEventListener("beforeinstallprompt", offer);
    window.addEventListener("appinstalled", done);
    return () => { window.removeEventListener("beforeinstallprompt", offer); window.removeEventListener("appinstalled", done); };
  }, []);
  if (installed) return null;
  if (prompt) {
    return (
      <button type="button" className="btn secondary" onClick={async () => {
        await prompt.prompt();
        const { outcome } = await prompt.userChoice;
        if (outcome === "accepted") setInstalled(true);
        setPrompt(null);
      }}>{t("pwa.install")}</button>
    );
  }
  if (!iosSafari()) return null;
  return (
    <>
      <button type="button" className="btn secondary" aria-expanded={hint} onClick={() => setHint((h) => !h)}>{t("pwa.install")}</button>
      {hint && <small role="status">{t("pwa.iosHint")}</small>}
    </>
  );
}
