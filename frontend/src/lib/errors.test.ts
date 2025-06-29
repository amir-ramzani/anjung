import { afterEach } from "vitest";

import i18n from "../i18n";
import { ApiError } from "../api/client";
import { blockerText, issueText } from "./errors";

afterEach(async () => { await i18n.changeLanguage("en"); });

describe("errors in the user's language (contract v0.16.0)", () => {
  it("translates field issues by code, with their values", async () => {
    expect(issueText({ issue: "String should have at least 12 characters", code: "string_too_short", params: { min_length: 12 } }))
      .toBe("Must be at least 12 characters.");
    await i18n.changeLanguage("ms");
    expect(issueText({ issue: "x", code: "string_too_short", params: { min_length: 12 } })).toBe("Mesti sekurang-kurangnya 12 aksara.");
    expect(issueText({ issue: "x", code: "notice_too_short", params: { days: 30, date: "2019-01-15" } }))
      .toBe("Perlu notis 30 hari: paling awal 15-01-2019. Atau ketepikan notis.");
    expect(issueText({ issue: "x", code: "must_not_be_before_from" })).toBe("Tidak boleh sebelum permulaan.");
    // the platform admin cannot set a company's logo (v0.17.0)
    expect(issueText({ issue: "is set by the org admin", code: "is_set_by_the_org_admin" }))
      .toBe("Logo dimuat naik oleh pentadbir syarikat di Tetapan → Penjenamaan.");
  });

  it("falls back to the API's English for an unknown code or none", () => {
    expect(issueText({ issue: "something new", code: "brand_new_rule" })).toBe("something new");
    expect(issueText({ issue: "no code at all" })).toBe("no code at all");
  });

  it("puts translated issues next to the fields", async () => {
    await i18n.changeLanguage("ms");
    const e = new ApiError(422, "validation_error", "Validation failed", [{ field: "email", issue: "value is not a valid email address", code: "invalid_email" }]);
    expect(e.fieldErrors).toEqual({ email: "Masukkan alamat e-mel yang sah." });
  });

  it("translates delete blockers, money included", async () => {
    await i18n.changeLanguage("ms");
    expect(blockerText({ code: "unpaid", message: "x", params: { amount_minor: 150000 } })).toMatch(/^Penyewa masih berhutang RM ?1,500\.00/);
    expect(blockerText({ code: "maintenance_history", message: "x", params: { what: "room", count: 1 } }))
      .toBe("Terdapat 1 permintaan penyelenggaraan bagi bilik ini. Padamnya dahulu, atau nyahaktifkan bilik ini.");
    expect(blockerText({ code: "unpaid", message: "English only" })).toBe("English only"); // no values: keep the English
  });
});
