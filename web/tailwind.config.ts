// Tokens mirror docs/style.md; values live in app/globals.css as CSS variables.
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--color-surface)",
        card: "var(--color-card)",
        border: "var(--color-border)",
        accent: "#25D366",
        accent2: "#00a8ff",
        primary: "var(--color-text-primary)",
        secondary: "var(--color-text-secondary)",
        muted: "var(--color-text-muted)",
        inverse: "var(--color-text-inverse)",
      },
    },
  },
  plugins: [],
};

export default config;
