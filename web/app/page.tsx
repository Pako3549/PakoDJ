"use client";

import { useEffect, useState } from "react";

type Health = { status: string; guilds: number };

// Same-origin call: nginx routes /api/* to the bot's FastAPI (no CORS needed).
async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    fetchHealth().then(setHealth);
  }, []);

  const running = health !== null;

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
            {/* note icon */}
            <svg viewBox="0 0 24 24" className="w-6 h-6 text-accent" fill="currentColor">
              <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
            </svg>
          </div>
          <div>
            <h1 className="text-lg font-semibold">PakoDJ</h1>
            <p className="text-xs text-muted">
              {running
                ? "Bot online - Web API reachable"
                : "Web API unreachable (log in later after Discord setup)"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${running ? "bg-accent" : "bg-red-500 pulse"}`} />
        </div>
      </header>

      <section className="bg-card border border-border rounded-xl p-4">
        <h2 className="text-sm font-medium mb-3">Status</h2>
        <dl className="space-y-2 text-sm">
          <div className="flex justify-between">
            <dt className="text-muted">API status</dt>
            <dd>{health?.status ?? "unknown"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted">Servers (guilds)</dt>
            <dd>{health?.guilds ?? "unknown"}</dd>
          </div>
        </dl>
      </section>
    </main>
  );
}
