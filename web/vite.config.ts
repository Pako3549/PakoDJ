import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));

// The dev stack reaches this server through nginx (one origin, / -> web:3000,
// /api/* -> bot). The SPA fallback is what makes /guilds/:id deep links work.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(root, "src"),
    },
  },
  server: {
    // nginx forwards Host: localhost:8080; skip Vite host checks.
    allowedHosts: true,
  },
});
