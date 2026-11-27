import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config.ts";

// `npm test` runs the page's tests in jsdom; `npm run coverage` also fails the run below 85% on any measure.
// Two workers at most: this Mac runs model jobs beside it.
export default mergeConfig(viteConfig, defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    maxWorkers: 2,
    restoreMocks: true,
    // The stylesheet is read as text by a layout test (`styles.css?raw`); without this every CSS import is empty
    css: { include: [/styles\.css/] },
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**"],
      reporter: ["text", "text-summary"],
      thresholds: { lines: 85, statements: 85, functions: 85, branches: 85 },
    },
  },
}));
