import { afterEach, vi } from "vitest";

import { copyText } from "./clipboard";

const secure = (on: boolean) => Object.defineProperty(window, "isSecureContext", { value: on, configurable: true });
const execCommand = (result: boolean | Error) => {
  const fn = vi.fn(() => { if (result instanceof Error) throw result; return result; });
  Object.defineProperty(document, "execCommand", { value: fn, configurable: true });
  return fn;
};

afterEach(() => { vi.restoreAllMocks(); secure(true); });

describe("copyText", () => {
  it("uses the Clipboard API on a secure origin", async () => {
    secure(true);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const exec = execCommand(true);
    expect(await copyText("http://x/invite/abc")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("http://x/invite/abc");
    expect(exec).not.toHaveBeenCalled();
  });

  it("falls back to execCommand on plain HTTP, where the Clipboard API is missing", async () => {
    secure(false);
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    let copied = "";
    const exec = execCommand(true);
    exec.mockImplementation(() => { copied = (document.activeElement as HTMLTextAreaElement).value; return true; });
    expect(await copyText("http://192.168.0.50/invite/abc")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(copied).toBe("http://192.168.0.50/invite/abc");
    expect(document.querySelector("textarea")).toBeNull(); // cleaned up
  });

  it("falls back when the Clipboard API refuses", async () => {
    secure(true);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    execCommand(true);
    expect(await copyText("x")).toBe(true);
  });

  it("reports failure instead of pretending it copied", async () => {
    secure(false);
    execCommand(false);
    expect(await copyText("x")).toBe(false);
    execCommand(new Error("nope"));
    expect(await copyText("x")).toBe(false);
  });
});
