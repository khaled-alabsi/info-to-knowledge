import { describe, expect, it } from "vitest";
import { buildVocabulary, completeWord, findTranscriptMatches } from "./transcriptSearch";

const LINES = [
  { start_s: 0, end_s: 4, text: "Jonas opens the gateway." },
  { start_s: 4, end_s: 9, text: "The token goes to the provider." },
  { start_s: 9, end_s: 15, text: "Then the provider sends the token back to the gateway." },
  { start_s: 15, end_s: 20, text: "Die Überweisung wird ausgeführt." },
];

const matchedLines = (query: string) => findTranscriptMatches(LINES, query).map((match) => match.index);

describe("findTranscriptMatches", () => {
  it("finds every line holding all the words, the line holding them as one phrase first", () => {
    expect(matchedLines("the provider")).toEqual([1, 2]);
    expect(matchedLines("token gateway")).toEqual([2]);
    expect(matchedLines("sends token")[0]).toBe(2);
  });

  it("matches a word begun but not finished", () => {
    expect(matchedLines("provi")).toEqual([1, 2]);
  });

  it("forgives a letter wrong, missing or swapped, but not a different word", () => {
    expect(matchedLines("provder")).toEqual([1, 2]);
    expect(matchedLines("gatewya")).toEqual([0, 2]);
    expect(matchedLines("printer")).toEqual([]);
  });

  it("ignores case and accents", () => {
    expect(matchedLines("UBERWEISUNG")).toEqual([3]);
  });

  it("puts an exact word before a near spelling", () => {
    const lines = [{ start_s: 0, end_s: 1, text: "the tokens" }, { start_s: 1, end_s: 2, text: "the token" }];
    expect(findTranscriptMatches(lines, "token").map((match) => match.index)).toEqual([1, 0]);
  });

  it("gives where each matched word sits in the line's own text, so the page can mark it", () => {
    const [match] = findTranscriptMatches(LINES, "jonas gatway");
    expect(match.ranges.map(([start, end]) => LINES[0].text.slice(start, end))).toEqual(["Jonas", "gateway"]);
  });

  it("returns nothing for an empty query, and no more lines than asked", () => {
    expect(findTranscriptMatches(LINES, "  ")).toEqual([]);
    expect(findTranscriptMatches(LINES, "the", 2)).toHaveLength(2);
  });
});

describe("completeWord", () => {
  const vocabulary = buildVocabulary(LINES);

  it("completes the last word typed with the transcript's most frequent word that begins with it", () => {
    expect(completeWord(vocabulary, "the prov")).toBe("ider");
    expect(completeWord(vocabulary, "Gate")).toBe("way");
  });

  it("completes nothing for a finished word, a space at the end, or a word the transcript does not hold", () => {
    expect(completeWord(vocabulary, "token")).toBe("");
    expect(completeWord(vocabulary, "the ")).toBe("");
    expect(completeWord(vocabulary, "printe")).toBe("");
    expect(completeWord(vocabulary, "")).toBe("");
  });
});
