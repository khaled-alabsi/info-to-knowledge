import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The page as the end-to-end tests serve it: the app's own build settings with no /api proxy, so a call no test
// answers fails instead of reaching the real `i2k serve` and its runs.
export default defineConfig({
  root: new URL("..", import.meta.url).pathname,
  plugins: [react()],
});
