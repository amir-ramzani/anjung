import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import i18n from "../i18n";

afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  sessionStorage.clear();
  localStorage.clear();
  await i18n.changeLanguage("en"); // the language is global state: never let one test leak it into the next
});
