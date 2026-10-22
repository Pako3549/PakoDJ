import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, clearToken } from "@/lib/api";
import { useAccount } from "@/lib/auth";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type Guild = { id: string; name: string };

type Status = { playing: boolean; queue_count: number; now_playing: { title: string } | null };

export default function DashboardPage() {
  const { account, loading, error } = useAccount();
  const [guilds, setGuilds] = useState<Guild[]>([]);
  const [statuses, setStatuses] = useState<Record<string, Status | null>>({});

  useEffect(() => {
    if (!account) return;
    apiFetch<Guild[]>("/api/guilds")
      .then((gs) => {
        setGuilds(gs);
        gs.forEach((g) => {
          apiFetch<Status>(`/api/guilds/${g.id}/status`)
            .then((s) => setStatuses((prev) => ({ ...prev, [g.id]: s })))
            .catch(() => setStatuses((prev) => ({ ...prev, [g.id]: null })));
        });
      })
      .catch(() => {});
  }, [account]);

  if (loading || !account) return error ? <ErrorBox message={error} /> : null;

  const playing = Object.values(statuses).filter((s) => s?.playing).length;
  const queued = Object.values(statuses).reduce((sum, s) => sum + (s?.queue_count ?? 0), 0);

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
            <svg viewBox="0 0 24 24" className="w-6 h-6 text-accent" fill="currentColor">
              <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z" />
            </svg>
          </div>
          <div>
            <h1 className="text-lg font-semibold">PakoDJ</h1>
            <p className="text-xs text-muted">
              signed in as {account.username} · {account.role}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {account.role === "admin" && (
            <Link
              to="/users"
              className="px-4 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition"
            >
              Users
            </Link>
          )}
          <button
            onClick={() => {
              clearToken();
              window.location.href = "/login";
            }}
            className="px-4 py-2 rounded-lg bg-red-500/10 text-red-400 text-sm font-medium hover:bg-red-500/20 transition"
          >
            Logout
          </button>
          <ThemeToggle />
        </div>
      </header>

      <div className="grid grid-cols-6 gap-3 mb-6">
        <div className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted mb-1">Servers</p>
          <p className="text-2xl font-bold">{guilds.length}</p>
        </div>
        <div className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted mb-1">Playing</p>
          <p className="text-2xl font-bold text-accent">{playing}</p>
        </div>
        <div className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted mb-1">Queued</p>
          <p className="text-2xl font-bold">{queued}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {guilds.map((g) => {
          const s = statuses[g.id];
          return (
            <Link
              key={g.id}
              to={`/guilds/${g.id}`}
              className="bg-card border border-border rounded-xl p-4 hover:border-accent/60 transition"
            >
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium truncate">{g.name}</p>
                <span
                  className={`w-2 h-2 rounded-full ${
                    s?.playing ? "bg-accent" : "bg-muted"
                  }`}
                />
              </div>
              <p className="text-xs text-secondary truncate">
                {s?.now_playing?.title ?? (s === null ? "unreachable" : "idle")}
              </p>
              <p className="text-xs text-muted mt-1">{s?.queue_count ?? 0} in queue</p>
            </Link>
          );
        })}
      </div>
    </main>
  );
}
