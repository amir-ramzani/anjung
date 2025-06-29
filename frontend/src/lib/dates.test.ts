import { formatDate, formatDateTime, formatMonth } from "./dates";

describe("dates", () => {
  it("shows calendar dates as DD-MM-YYYY without shifting them", () => {
    expect(formatDate("2019-11-20")).toBe("20-11-2019");
    expect(formatDate(null)).toBe("");
  });
  it("shows moments in local time", () => {
    const local = new Date(2018, 10, 21, 9, 5); // 21 Nov 2018 09:05 local
    expect(formatDate(local.toISOString())).toBe("21-11-2018");
    expect(formatDateTime(local.toISOString())).toBe("21-11-2018 09:05");
  });
  it("shows billing months as MM-YYYY", () => {
    expect(formatMonth("2018-11")).toBe("11-2018");
  });
});
