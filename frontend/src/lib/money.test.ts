import { bpToPercent, formatMoney, myr, parseMoney, percentToBp } from "./money";

describe("money", () => {
  it("formats integers of sen", () => {
    expect(formatMoney(myr(150000))).toBe("RM 1,500.00");
    expect(formatMoney(5)).toBe("RM 0.05");
    expect(formatMoney(-12345)).toBe("-RM 123.45");
    expect(formatMoney(null)).toBe("");
  });
  it("parses amounts without floats", () => {
    expect(parseMoney("1500")).toBe(150000);
    expect(parseMoney("1,500.5")).toBe(150050);
    expect(parseMoney("RM 0.07")).toBe(7);
    expect(parseMoney("19.99")).toBe(1999); // 19.99 * 100 is 1998.9999999999998 in floating point
    expect(parseMoney("1.005")).toBeNull();
    for (const bad of ["", "abc", "-5", "1e3", "1.2.3", "99999999999999999999"]) expect(parseMoney(bad)).toBeNull();
  });
  it("round trips every amount from 0 to 100000 sen", () => {
    for (let n = 0; n <= 100000; n += 7) expect(parseMoney(formatMoney(n).replace("RM ", ""))).toBe(n);
  });
  it("handles basis points as integers", () => {
    expect(percentToBp("1.5")).toBe(150);
    expect(percentToBp("0.07")).toBe(7);
    expect(percentToBp("100")).toBe(10000);
    expect(percentToBp("100.01")).toBeNull();
    expect(percentToBp("1.555")).toBeNull();
    expect(bpToPercent(150)).toBe("1.50");
    expect(bpToPercent(7)).toBe("0.07");
  });
});
