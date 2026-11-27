// The run form's Range: times written and read as the run line takes them, m:ss or h:mm:ss, the
// same rule as the server's `input_range_service.parse_time` and `format_time`.

// A time as the page and the run line write it: m:ss, or h:mm:ss from an hour
export function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

// A typed time in seconds: "90", "12:30" or "1:05:00"; null when it is no time
export function parseClock(text: string): number | null {
  const fields = text.trim().split(":");
  if (!text.trim() || fields.length > 3) return null;
  const numbers = fields.map((field) => (/^\d+(\.\d+)?$/.test(field.trim()) ? Number(field) : NaN));
  if (numbers.some((value) => Number.isNaN(value)) || numbers.slice(1).some((value) => value >= 60)) return null;
  return numbers.reduce((total, value) => total * 60 + value, 0);
}

// A PDF's pages as the run line takes them, "12-40", or one page alone
export function formatPages(first: number, last: number): string {
  return first === last ? String(first) : `${first}-${last}`;
}

// "12-40" or "7" as [first, last]; null when it is no page range
export function parsePages(text: string): [number, number] | null {
  const match = /^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(text);
  if (!match) return null;
  const first = Number(match[1]);
  const last = match[2] ? Number(match[2]) : first;
  return first >= 1 && last >= first ? [first, last] : null;
}
