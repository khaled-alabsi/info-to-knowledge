import type { SpeedOptions } from "../api";

// The Workflow speed menu as the server answers it in `run-defaults`: two of its settings, its three presets, and the
// settings in force on Quality
const QUALITY = { "video.workflow": "quality", "framesProcessing.isCritiqueOn": true, "review.isOn": true };

export const SPEED_OPTIONS: SpeedOptions = {
  settings: [
    { key: "video.workflow", label: "Frame reading and writing", hint: "full notes or quick descriptions", kind: "choice",
      options: [{ value: "quality", label: "Full notes, chapter by chapter" },
        { value: "fast-processing", label: "Quick descriptions, one call" }] },
    { key: "framesProcessing.isCritiqueOn", label: "Critique", hint: "the model lists what went wrong", kind: "boolean",
      options: [] },
    { key: "review.isOn", label: "Review (phase 5)", hint: "a critic reads the document", kind: "boolean", options: [] },
  ],
  presets: { quality: QUALITY, balanced: { ...QUALITY, "framesProcessing.isCritiqueOn": false },
    fast: { "video.workflow": "fast-processing", "framesProcessing.isCritiqueOn": false, "review.isOn": false } },
  current: QUALITY,
  default_preset: "quality",
};
