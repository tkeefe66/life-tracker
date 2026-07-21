import { describe, expect, it } from "vitest";
import { targetLabel, weekLabel } from "./lib";

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
