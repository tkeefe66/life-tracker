import { describe, expect, it } from "vitest";
import { dayLabel, targetLabel, weekLabel } from "./lib";

describe("weekLabel", () => {
  it("formats a Monday week start as a Mon–Sun range", () => {
    expect(weekLabel("2026-07-13")).toBe("Jul 13 – Jul 19");
  });
  it("crosses month boundaries", () => {
    expect(weekLabel("2026-07-27")).toBe("Jul 27 – Aug 2");
  });
});

describe("targetLabel", () => {
  it("renders ceiling and floor", () => {
    expect(targetLabel("ceiling", 1)).toBe("≤1");
    expect(targetLabel("floor", 3)).toBe("≥3");
  });
});

describe("dayLabel", () => {
  it("formats a date with weekday and full month", () => {
    expect(dayLabel("2026-07-20")).toBe("Monday, July 20");
  });
});

import { addDays, relativeDayLabel } from "./lib";

describe("addDays", () => {
  it("steps forward and back across month boundaries", () => {
    expect(addDays("2026-07-01", -1)).toBe("2026-06-30");
    expect(addDays("2026-06-30", 1)).toBe("2026-07-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });
});

describe("relativeDayLabel", () => {
  it("labels today and yesterday", () => {
    expect(relativeDayLabel("2026-07-22", "2026-07-22")).toBe("Today");
    expect(relativeDayLabel("2026-07-21", "2026-07-22")).toBe("Yesterday");
  });
  it("labels older days with weekday and month", () => {
    expect(relativeDayLabel("2026-07-14", "2026-07-22")).toBe("Tue, Jul 14");
  });
  it("appends the year when it differs", () => {
    expect(relativeDayLabel("2025-12-31", "2026-07-22")).toBe("Wed, Dec 31, 2025");
  });
});

import { mondayOf } from "./lib";

describe("mondayOf", () => {
  it("maps a mid-week date to its containing Monday", () => {
    expect(mondayOf("2026-07-22")).toBe("2026-07-20"); // Wednesday
  });
  it("is identity on a Monday", () => {
    expect(mondayOf("2026-07-20")).toBe("2026-07-20");
  });
  it("maps Sunday back to the previous Monday", () => {
    expect(mondayOf("2026-07-26")).toBe("2026-07-20");
  });
  it("crosses year boundaries", () => {
    expect(mondayOf("2026-01-01")).toBe("2025-12-29"); // Thursday
  });
});
