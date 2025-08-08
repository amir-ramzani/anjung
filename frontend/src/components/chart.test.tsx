import { render, screen } from "@testing-library/react";

import { BarChart, barHeights } from "./Chart";

describe("barHeights", () => {
  it("scales to the tallest bar and never goes negative", () => {
    expect(barHeights([0, 50, 100], 100, 160)).toEqual([0, 80, 160]);
    expect(barHeights([-5, 10], 10, 100)).toEqual([0, 100]);
    expect(barHeights([5, 5], 0, 100)).toEqual([0, 0]); // an empty chart is flat, not NaN
  });
});

describe("BarChart", () => {
  it("is readable without sight: the label spells out every value", () => {
    render(<BarChart title="Income" labels={["2018-11", "2018-12"]} format={(n) => `RM ${n / 100}`}
      series={[{ name: "Invoiced", color: "#000", values: [150000, 160000] }, { name: "Collected", color: "#0f0", values: [150000, 0] }]} />);
    const img = screen.getByRole("img");
    expect(img).toHaveAccessibleName("Income. Invoiced: RM 1500, RM 1600. Collected: RM 1500, RM 0");
    expect(img.querySelectorAll("rect")).toHaveLength(4);
    expect(screen.getByText("Collected")).toBeInTheDocument();
  });
});
