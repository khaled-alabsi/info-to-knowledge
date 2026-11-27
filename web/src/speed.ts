import type { SpeedValue } from "./api";

// A Workflow speed preset's name as the page shows it: "balanced" as "Balanced"
export function nameSpeedPreset(preset: string): string {
  return preset ? preset.charAt(0).toUpperCase() + preset.slice(1) : "";
}

// A job's `KEY=VALUE` pairs of speed settings as the values the menu holds: "true" and "false" a switch, digits a count
export function readSpeedSettings(pairs: string[]): Record<string, SpeedValue> {
  return Object.fromEntries(pairs.map((pair) => {
    const at = pair.indexOf("=");
    const text = pair.slice(at + 1);
    const value: SpeedValue = text === "true" ? true : text === "false" ? false : /^\d+$/.test(text) ? Number(text) : text;
    return [pair.slice(0, at), value];
  }));
}
