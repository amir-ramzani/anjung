import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { STAFF, useAuth, type Role } from "../auth/AuthContext";
import { LANGUAGES, setLanguage, type Lang } from "../i18n";
import { BrandMark, rememberHandle, rememberedHandle, useApplyBrand } from "../lib/brand";
import { formatDate } from "../lib/dates";
import { InstallApp } from "../lib/pwa";
import { Loading } from "./ui";

interface NavItem { to: string; label: string; roles: Role[] }

const NAV: NavItem[] = [
  { to: "/", label: "nav.dashboard", roles: ["org_admin", "property_manager", "tenant"] },
  { to: "/properties", label: "nav.properties", roles: STAFF },
  { to: "/tenants", label: "nav.tenants", roles: STAFF },
  { to: "/leases", label: "nav.leases", roles: [...STAFF, "tenant"] },
  { to: "/invoices", label: "nav.invoices", roles: [...STAFF, "tenant"] },
  { to: "/payments/unmatched", label: "nav.unmatched", roles: ["org_admin"] },
  { to: "/maintenance", label: "nav.maintenance", roles: [...STAFF, "tenant"] },
  { to: "/reports", label: "nav.reports", roles: STAFF },
  { to: "/users", label: "nav.users", roles: ["org_admin"] },
  { to: "/settings", label: "nav.settings", roles: ["org_admin"] },
  { to: "/audit", label: "nav.audit", roles: ["org_admin"] },
  { to: "/privacy", label: "nav.privacy", roles: ["org_admin", "tenant"] },
  { to: "/documents", label: "nav.documents", roles: ["tenant"] },
  { to: "/preferences", label: "nav.preferences", roles: ["tenant"] },
  { to: "/platform/organizations", label: "nav.organisations", roles: ["platform_admin"] },
];

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  return (
    <select aria-label={t("nav.language")} value={i18n.language} onChange={(e) => setLanguage(e.target.value as Lang)}>
      {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
    </select>
  );
}

/** Everything under it needs a signed-in user. Pass `roles` to restrict a branch further. */
export function Protected({ roles }: { roles?: Role[] }) {
  const { status, me } = useAuth();
  const loc = useLocation();
  const { t } = useTranslation();
  if (status === "loading") return <Loading />;
  if (status === "anonymous") {
    // signed out or session expired: back to the company's own sign-in page if this browser has one
    const handle = rememberedHandle();
    return <Navigate to={handle ? `/login/${handle}` : "/login"} state={{ from: loc.pathname }} replace />;
  }
  if (roles && me && !roles.some((r) => me.roles.includes(r))) return <p role="alert" className="error">{t("common.forbidden")}</p>;
  return <Outlet />;
}

export function Layout() {
  const { me, signOut, has } = useAuth();
  const { t } = useTranslation();
  const { pathname } = useLocation();
  // On narrow screens the links fold behind a menu button; it closes again on every navigation.
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  // After moving to another page, put keyboard and screen-reader focus on its heading (or the content), so the
  // new page is announced and Tab starts from it. Not on the first page: focus stays at the top there.
  const shown = useRef(pathname);
  useEffect(() => {
    if (shown.current === pathname) return; // the first page (React may run this twice in development)
    shown.current = pathname;
    const t = window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>("main h1") ?? document.querySelector<HTMLElement>("main");
      target?.focus({ preventScroll: true });
    }, 50);
    return () => window.clearTimeout(t);
  }, [pathname]);
  // the company's brand (contract v0.11.0); remembered so sign-out returns to its own sign-in page
  const brand = me?.branding ?? null;
  useApplyBrand(brand);
  useEffect(() => { if (me) rememberHandle(brand?.handle ?? null); }, [me, brand?.handle]);
  return (
    <div className="shell">
      <a className="skip" href="#main">{t("nav.skip")}</a>
      <nav aria-label={t("nav.main")} className={open ? "open" : undefined}>
        <div className="nav-bar">
          <BrandMark brand={brand} />
          <button type="button" className="btn menu-toggle" aria-expanded={open} aria-controls="nav-menu" onClick={() => setOpen((o) => !o)}>
            {open ? t("nav.close") : t("nav.menu")}
          </button>
        </div>
        <ul id="nav-menu">
          {NAV.filter((n) => has(...n.roles)).map((n) => (
            <li key={n.to}><NavLink to={n.to} end={n.to === "/"}>{t(n.label)}</NavLink></li>
          ))}
        </ul>
        <div className="nav-foot">
          <LanguageSwitch />
          <InstallApp />
          <small>{me?.email}</small>
          <button className="btn secondary" onClick={signOut}>{t("nav.signOut")}</button>
        </div>
      </nav>
      <main id="main" tabIndex={-1}>
        {me?.plan?.status === "read_only" && (
          <p role="status" className="notice plan-banner">
            {t("plan.readOnly", { date: formatDate(me.plan.active_until), until: formatDate(me.plan.read_only_until) })}
          </p>
        )}
        <Outlet />
      </main>
    </div>
  );
}
