import { describe, expect, it } from "vitest";
import { fmtAgo, fmtNum, fmtPct, fmtPrice, fmtSignedUsd, fmtUsd, priceDigits, toneOf } from "./format";

describe("format", () => {
  it("never prints a number for missing values", () => {
    expect(fmtNum(null)).toBe("—");
    expect(fmtUsd(undefined)).toBe("—");
    expect(fmtPct(Number.NaN)).toBe("—");
    expect(fmtPrice(Infinity)).toBe("—");
  });
  it("formats money and percentages", () => {
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
    expect(fmtUsd(-3.2)).toBe("-$3.20");
    expect(fmtSignedUsd(5)).toBe("+$5.00");
    expect(fmtPct(1.234, 2, true)).toBe("+1.23%");
    expect(fmtPct(-0.5, 1, true)).toBe("-0.5%");
  });
  it("chooses price precision by magnitude", () => {
    expect(priceDigits(65000)).toBe(2);
    expect(priceDigits(150)).toBe(3);
    expect(priceDigits(0.25)).toBe(6);
  });
  it("describes ages and tones", () => {
    expect(fmtAgo(5)).toBe("5s ago");
    expect(fmtAgo(120)).toBe("2m ago");
    expect(fmtAgo(null)).toBe("never");
    expect(toneOf(2)).toBe("up");
    expect(toneOf(-1)).toBe("down");
    expect(toneOf(0)).toBe("flat");
  });
});
