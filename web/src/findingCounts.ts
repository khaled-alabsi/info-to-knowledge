// A rejection's findings as their codes, each once with how often it came ("MODEL_CALL_FAILED ×5"): one failure
// repeated for each frame of a batch, or a long fix text, filled a phone's screen (the owner: "too big and not needed");
// the steps tables' Copy keeps every finding whole
export function countFindingCodes(findings: { code: string }[]): { code: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const finding of findings) counts.set(finding.code, (counts.get(finding.code) ?? 0) + 1);
  return [...counts].map(([code, count]) => ({ code, count }));
}
