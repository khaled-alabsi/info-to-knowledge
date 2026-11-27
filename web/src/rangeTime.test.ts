import { describe, expect, it } from "vitest";
import { formatClock, formatPages, parseClock, parsePages } from "./rangeTime";
import { describeRange } from "./components/RangeEditor";

describe("the Range's times and pages", () => {
  it("writes a time as m:ss, and as h:mm:ss from an hour", () => {
    expect(formatClock(65)).toBe("1:05");
    expect(formatClock(3900)).toBe("1:05:00");
    expect(formatClock(-3)).toBe("0:00");
  });

  it("reads a typed time as the run line does, and refuses what is no time", () => {
    expect(parseClock("90")).toBe(90);
    expect(parseClock("12:30")).toBe(750);
    expect(parseClock("1:05:00")).toBe(3900);
    for (const wrong of ["", "ten", "1:75", "1:2:3:4", "-3"]) expect(parseClock(wrong)).toBeNull();
  });

  it("writes and reads a PDF's pages as first-last", () => {
    expect(formatPages(3, 8)).toBe("3-8");
    expect(formatPages(7, 7)).toBe("7");
    expect(parsePages(" 3 - 8 ")).toEqual([3, 8]);
    expect(parsePages("7")).toEqual([7, 7]);
    for (const wrong of ["", "0-3", "8-3", "a"]) expect(parsePages(wrong)).toBeNull();
  });

  it("names on the Range button the whole input or the part chosen", () => {
    const none = { range_start: "", range_end: "", range_pages: "" };
    expect(describeRange(none, 600, 0)).toBe("Whole recording");
    expect(describeRange({ ...none, range_start: "0:20" }, 600, 0)).toBe("0:20 to 10:00");
    expect(describeRange({ ...none, range_end: "5:00" }, 600, 0)).toBe("0:00 to 5:00");
    expect(describeRange(none, 0, 10)).toBe("All 10 pages");
    expect(describeRange({ ...none, range_pages: "3-6" }, 0, 10)).toBe("Pages 3 to 6");
    expect(describeRange({ ...none, range_pages: "4" }, 0, 10)).toBe("Page 4");
  });
});
