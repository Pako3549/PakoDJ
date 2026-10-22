"use client";

import { useEffect, useState } from "react";

// Theme toggle per docs/style.md: dark default, light opt-in, persisted.
export default function ThemeToggle() {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }

  return (
    <button
      onClick={toggle}
      aria-label="Toggle theme"
      className="p-2 rounded-lg bg-card border border-border text-secondary hover:text-primary transition"
    >
      {dark ? (
        // moon = dark mode is active
        <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
          <path d="M12.3 4.9c.4-.2.6-.7.5-1.1s-.6-.8-1-.8C6.8 3.4 3 7.3 3 12c0 5 4 9 9 9 4.7 0 8.6-3.8 9-8.8.1-.5-.3-.9-.8-1-.4-.1-.9.1-1.1.5-1 1.4-2.7 2.3-4.6 2.3-3.2 0-5.9-2.6-5.9-5.9 0-1.4.5-2.7 1.2-3.2z" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
          <path d="M12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-13a1 1 0 0 1-1 1V3a1 1 0 0 1 1 1zm0 16a1 1 0 0 1 1 1v2a1 1 0 0 1-2 0v-2a1 1 0 0 1 1-1zM3 11a1 1 0 0 1 1 1H2a1 1 0 0 1 1-1zm18 0a1 1 0 0 1-1 1v-2a1 1 0 0 1 1 1zM5 5a1 1 0 0 1-1 1L2.6 4.6A1 1 0 0 1 4 3.2L5.4 4.6A1 1 0 0 1 5 5zm14 0a1 1 0 0 1 .4-.4l1.4-1.4a1 1 0 0 1 1.4 1.4L20.8 6.4A1 1 0 0 1 19 5zm-14 14a1 1 0 0 1 0 1.4l-1.4 1.4a1 1 0 0 1-1.4-1.4L3.6 19a1 1 0 0 1 1.4 0zm14-0a1 1 0 0 1 1.4 1.4l-1.4 1.4a1 1 0 0 1-1.4-1.4L19 19z" />
        </svg>
      )}
    </button>
  );
}
