import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, unwrap, type Schemas } from "../api/client";
import { config } from "../config";

/** A company's public brand (contract v0.11.0). Each company is its own organisation. */
export type Brand = Schemas["PublicBrand"];

const KEY = "anjung.brand";
const HANDLE = /^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$/;

/** The handle of the company sign-in page this browser last used, so sign-out and an expired session return there. */
export function rememberedHandle(): string | null {
  try {
    const h = localStorage.getItem(KEY);
    return h && HANDLE.test(h) ? h : null;
  } catch {
    return null;
  }
}

export function rememberHandle(handle: string | null): void {
  try {
    if (handle) localStorage.setItem(KEY, handle);
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: nothing to remember */
  }
}

/** The public brand behind a handle, or null (unknown, switched off). Nothing is fetched without a handle. */
export function usePublicBrand(handle: string | null | undefined) {
  const valid = !!handle && HANDLE.test(handle);
  return useQuery({
    queryKey: ["brand", handle],
    enabled: valid,
    retry: false,
    staleTime: 5 * 60_000,
    queryFn: () => unwrap(api.GET("/v1/brands/{handle}", { params: { path: { handle: handle! } } })),
  });
}

const channel = (v: number) => {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
};

/** White text on the colour must stay readable (WCAG AA, 4.5:1), or the default teal is kept. */
export function readableOnWhite(hex: string | null | undefined): boolean {
  if (!hex || !/^#[0-9a-fA-F]{6}$/.test(hex)) return false;
  return 1.05 / (luminance(hex) + 0.05) >= 4.5;
}

function darker(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const f = (v: number) => Math.max(0, Math.round(v * 0.8)).toString(16).padStart(2, "0");
  return `#${f((n >> 16) & 255)}${f((n >> 8) & 255)}${f(n & 255)}`;
}

let siteName = "Anjung";
let pageTitle = "";

/** The browser tab says which page this is, then the company (or Anjung): screen readers announce it too. */
export function setPageTitle(title: string): void {
  pageTitle = title;
  document.title = title ? `${title} · ${siteName}` : siteName;
}

/** Puts the brand on the page: the accent colour on buttons and the company name (both themes keep white text
 * on it, so a colour too light for that is ignored), and the tab title. */
export function useApplyBrand(brand: Brand | null | undefined): void {
  const colour = brand?.accent_colour;
  const name = brand?.display_name;
  useEffect(() => {
    const root = document.documentElement.style;
    const vars = ["--primary", "--primary-hover", "--brand"];
    if (colour && readableOnWhite(colour)) {
      root.setProperty("--primary", colour);
      root.setProperty("--primary-hover", darker(colour));
      root.setProperty("--brand", colour);
    }
    siteName = name ?? "Anjung";
    document.title = pageTitle ? `${pageTitle} · ${siteName}` : siteName;
    return () => {
      vars.forEach((v) => root.removeProperty(v));
      siteName = "Anjung";
      document.title = pageTitle ? `${pageTitle} · Anjung` : "Anjung";
    };
  }, [colour, name]);
}

/** The company's logo, or its name when there is no logo; plain "Anjung" without branding. */
export function BrandMark({ brand, className = "brand" }: { brand: Brand | null | undefined; className?: string }) {
  if (!brand) return <strong className={className}>Anjung</strong>;
  return brand.logo_url
    ? <img className={`${className} brand-logo`} src={`${config.apiBaseUrl}${brand.logo_url}`} alt={brand.display_name} />
    : <strong className={className}>{brand.display_name}</strong>;
}
