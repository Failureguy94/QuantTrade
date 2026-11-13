import { describe, expect, it } from "vitest";

import {
  drawdowns,
  formatDate,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  humanize,
  signClass,
  strategyLabel,
  toNumber,
} from "./format";

describe("toNumber", () => {
  it("reads the API's decimal strings", () => {
    expect(toNumber("99975.28676919")).toBeCloseTo(99975.28676919);
    expect(toNumber("0.00000000")).toBe(0);
    expect(toNumber(12)).toBe(12);
  });

  it("returns null for missing or unreadable values", () => {
    for (const value of [null, undefined, "", "abc", Number.NaN, Infinity]) {
      expect(toNumber(value)).toBeNull();
    }
  });
});

describe("formatMoney", () => {
  it("formats in the given currency", () => {
    expect(formatMoney("1234.5")).toBe("$1,234.50");
    expect(formatMoney("1234.5", "INR")).toBe("₹1,234.50");
    expect(formatMoney(-50)).toBe("-$50.00");
  });

  it("can show an explicit sign, but not for zero", () => {
    expect(formatMoney("12.3", "USD", { sign: true })).toBe("+$12.30");
    expect(formatMoney("-12.3", "USD", { sign: true })).toBe("-$12.30");
    expect(formatMoney("0", "USD", { sign: true })).toBe("$0.00");
  });

  it("abbreviates when compact", () => {
    expect(formatMoney(135000, "USD", { compact: true })).toBe("$135K");
  });

  it("shows a dash for nothing", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("")).toBe("—");
  });
});

describe("formatPercent", () => {
  it("treats the value as a fraction", () => {
    expect(formatPercent(0.1234)).toBe("12.34%");
    expect(formatPercent(-0.3363)).toBe("-33.63%");
    expect(formatPercent(0.46, { digits: 0 })).toBe("46%");
    expect(formatPercent(0.05, { sign: true })).toBe("+5.00%");
  });

  it("shows a dash when the API had no value", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatPercent(Number.NaN)).toBe("—");
  });
});

describe("other formatters", () => {
  it("formats numbers, ratios and dates", () => {
    expect(formatNumber("1234.56789", 4)).toBe("1,234.5679");
    expect(formatRatio(0.379)).toBe("0.38");
    expect(formatRatio(null)).toBe("—");
    // Dates are shown in UTC, the timezone bars are stamped in.
    expect(formatDate("2024-12-31T00:00:00+00:00")).toBe("Dec 31, 2024");
    expect(formatDate("2024-01-01")).toBe("Jan 1, 2024");
    expect(formatDate(null)).toBe("—");
  });

  it("makes codes readable", () => {
    expect(humanize("MONTE_CARLO")).toBe("Monte carlo");
    expect(humanize("split_and_dividend")).toBe("Split and dividend");
    expect(strategyLabel("SMA_CROSSOVER")).toBe("SMA crossover");
    expect(strategyLabel("sma_crossover")).toBe("SMA crossover");
    expect(strategyLabel("SOMETHING_NEW")).toBe("Something new");
  });

  it("colours gains and losses, and nothing else", () => {
    expect(signClass("12.5")).toContain("emerald");
    expect(signClass(-0.1)).toContain("red");
    expect(signClass(0)).toBe("");
    expect(signClass(null)).toBe("");
  });
});

describe("drawdowns", () => {
  it("measures the fall from the running peak", () => {
    const result = drawdowns([100, 120, 90, 120, 150, 75]);
    expect(result[0]).toBe(0);
    expect(result[1]).toBe(0);
    expect(result[2]).toBeCloseTo(-0.25);
    expect(result[3]).toBe(0);
    expect(result[4]).toBe(0);
    expect(result[5]).toBeCloseTo(-0.5);
  });

  it("handles an empty series", () => {
    expect(drawdowns([])).toEqual([]);
  });
});
