import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AppearanceCard } from "./AppearanceCard";
import { applyTheme, getTheme, setTheme } from "../lib/theme";
import "../i18n";

afterEach(() => { document.documentElement.removeAttribute("data-theme"); });

describe("theme", () => {
  it("follows the device until a choice is made", () => {
    expect(getTheme()).toBe("system");
    applyTheme(getTheme());
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("remembers dark and light, and goes back to the device on request", () => {
    setTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(getTheme()).toBe("dark");
    setTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    setTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(localStorage.getItem("anjung.theme")).toBeNull();
  });

  it("ignores a stored value it does not know", () => {
    localStorage.setItem("anjung.theme", "purple");
    expect(getTheme()).toBe("system");
  });

  it("the settings control changes the theme and shows the saved one", async () => {
    setTheme("dark");
    render(<AppearanceCard />);
    const select = screen.getByLabelText("Theme") as HTMLSelectElement;
    expect(select.value).toBe("dark");
    await userEvent.selectOptions(select, "light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem("anjung.theme")).toBe("light");
  });
});
