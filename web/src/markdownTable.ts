// A table cell's text for a markdown table pasted into a chat or a note: its pipes escaped, its lines joined
export function formatMarkdownCell(text: string): string {
  return text.replace(/\|/g, "\\|").replace(/\s+/g, " ").trim();
}
