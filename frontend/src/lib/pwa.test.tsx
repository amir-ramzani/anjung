import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";

import "../i18n";
import { InstallApp } from "./pwa";

afterEach(() => vi.unstubAllGlobals());

describe("installing the app", () => {
  it("offers the browser's install prompt from its own button", async () => {
    render(<InstallApp />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument(); // nothing until the browser offers it
    const prompt = vi.fn(async () => undefined);
    const e = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), { prompt, userChoice: Promise.resolve({ outcome: "accepted" }) });
    act(() => { window.dispatchEvent(e); });
    expect(e.defaultPrevented).toBe(true); // no mini-infobar: the app decides when to ask
    await userEvent.click(screen.getByRole("button", { name: "Install the app" }));
    expect(prompt).toHaveBeenCalled();
    expect(screen.queryByRole("button")).not.toBeInTheDocument(); // installed
  });

  it("tells iPhone users how to add it, since Safari has no prompt", async () => {
    vi.stubGlobal("navigator", { ...navigator, userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1" });
    render(<InstallApp />);
    await userEvent.click(screen.getByRole("button", { name: "Install the app" }));
    expect(screen.getByText(/tap Share, then “Add to Home Screen”/)).toBeInTheDocument();
  });

  it("has a manifest with the icons browsers need to install it", () => {
    const m = JSON.parse(readFileSync("public/manifest.webmanifest", "utf8"));
    expect(m.display).toBe("standalone");
    expect(m.icons.map((i: { sizes: string; purpose: string }) => `${i.sizes} ${i.purpose}`)).toEqual(["192x192 any", "512x512 any", "512x512 maskable"]);
    const sw = readFileSync("public/sw.js", "utf8");
    expect(sw).toMatch(/\(v1\|dev\|anjung-docs\)/); // the API and documents are never cached
  });
});
