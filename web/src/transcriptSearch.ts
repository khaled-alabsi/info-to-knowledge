// Searching phase 1's transcript as the Recording tab does it: the lines that hold every word typed, the closest first,
// and the word being typed completed from the transcript's own words. Case and accents never count.

export interface TranscriptLine {
  start_s: number;
  end_s: number;
  text: string;
  // "Speaker A" …, placed by phase 1's diarization; null for speech two people said at once, absent without it
  speaker?: string | null;
}

// One line that matched: its place in the transcript, how well it matched, and where in its text each matched word is.
export interface TranscriptMatch {
  index: number;
  score: number;
  ranges: [number, number][];
}

interface Token {
  word: string;
  start: number;
  end: number;
}

// How close a typed word is to a word of the line, from the closest down; a word near no word of a line drops the line.
const EXACT = 4;
const BEGINS = 3;
const HOLDS = 2;
const NEAR = 1;
// The whole query as consecutive words of a line.
const PHRASE_BONUS = 3;
const WORD = /[\p{L}\p{N}]+/gu;

function normalizeWord(word: string): string {
  return word.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

function splitWords(text: string): Token[] {
  return [...text.matchAll(WORD)].map((found) => ({
    word: normalizeWord(found[0]),
    start: found.index ?? 0,
    end: (found.index ?? 0) + found[0].length,
  }));
}

// The letters a typed word may get wrong and still match: none up to 3 letters, one up to 6, then two.
function countAllowedEdits(word: string): number {
  if (word.length <= 3) return 0;
  return word.length <= 6 ? 1 : 2;
}

// Edits (a letter added, dropped, changed, or two letters swapped) from one word to the other, or `limit + 1` once
// more than `limit` are needed.
function countEdits(typed: string, word: string, limit: number): number {
  if (Math.abs(typed.length - word.length) > limit) return limit + 1;
  let before: number[] = [];
  let previous = Array.from({ length: word.length + 1 }, (_, at) => at);
  for (let row = 1; row <= typed.length; row++) {
    const current = [row];
    let rowBest = row;
    for (let column = 1; column <= word.length; column++) {
      const cost = typed[row - 1] === word[column - 1] ? 0 : 1;
      let value = Math.min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + cost);
      if (row > 1 && column > 1 && typed[row - 1] === word[column - 2] && typed[row - 2] === word[column - 1]) {
        value = Math.min(value, before[column - 2] + 1);
      }
      current.push(value);
      rowBest = Math.min(rowBest, value);
    }
    if (rowBest > limit) return limit + 1;
    before = previous;
    previous = current;
  }
  return previous[word.length];
}

function scoreWord(typed: string, word: string): number {
  if (word === typed) return EXACT;
  if (word.startsWith(typed)) return BEGINS;
  if (typed.length >= 3 && word.includes(typed)) return HOLDS;
  const limit = countAllowedEdits(typed);
  return limit && countEdits(typed, word, limit) <= limit ? NEAR : 0;
}

function holdsPhrase(tokens: Token[], typed: string[]): boolean {
  return typed.length > 1 && tokens.some((_, at) => typed.every((word, offset) => tokens[at + offset]?.word === word));
}

// The lines holding every typed word, exactly or nearly, the best `limit` of them: the closest words first, a line
// holding the query as one phrase before one holding its words apart, and the earlier line on a tie.
export function findTranscriptMatches(lines: TranscriptLine[], query: string, limit = 8): TranscriptMatch[] {
  const typed = splitWords(query).map((token) => token.word);
  if (!typed.length) return [];
  const matches: TranscriptMatch[] = [];
  lines.forEach((line, index) => {
    const tokens = splitWords(line.text);
    let score = 0;
    const ranges: [number, number][] = [];
    for (const word of typed) {
      let best: Token | null = null;
      let bestScore = 0;
      for (const token of tokens) {
        const tokenScore = scoreWord(word, token.word);
        if (tokenScore > bestScore) {
          best = token;
          bestScore = tokenScore;
        }
      }
      if (!best) return;
      score += bestScore;
      ranges.push([best.start, best.end]);
    }
    if (holdsPhrase(tokens, typed)) score += PHRASE_BONUS;
    matches.push({ index, score, ranges: ranges.sort((left, right) => left[0] - right[0]) });
  });
  return matches.sort((left, right) => right.score - left.score || left.index - right.index).slice(0, limit);
}

// Every word of the transcript by its letters without case or accents, with the spelling heard most and how often.
export type Vocabulary = Map<string, { word: string; count: number }>;

export function buildVocabulary(lines: TranscriptLine[]): Vocabulary {
  const vocabulary: Vocabulary = new Map();
  for (const line of lines) {
    for (const found of line.text.matchAll(WORD)) {
      const key = normalizeWord(found[0]);
      const known = vocabulary.get(key);
      vocabulary.set(key, { word: known?.word ?? found[0].toLowerCase(), count: (known?.count ?? 0) + 1 });
    }
  }
  return vocabulary;
}

// The letters that finish the last word typed: the rest of the most frequent transcript word beginning with it, or
// nothing when the word is already finished, is followed by a space, or begins no word of the transcript.
export function completeWord(vocabulary: Vocabulary, query: string): string {
  const typed = query.match(/[\p{L}\p{N}]+$/u)?.[0];
  if (!typed) return "";
  const prefix = normalizeWord(typed);
  let best: { key: string; word: string; count: number } | null = null;
  for (const [key, entry] of vocabulary) {
    if (key.startsWith(prefix) && (!best || entry.count > best.count)) best = { key, ...entry };
  }
  return best && best.key !== prefix ? best.word.slice(typed.length) : "";
}
