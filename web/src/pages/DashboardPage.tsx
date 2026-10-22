import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, clearToken } from "@/lib/api";
import { useAccount } from "@/lib/auth";
import { readCache, writeCache } from "@/lib/cache";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";
import RequestAccessPanel from "@/components/RequestAccessPanel";

type Guild = { id: string; name: string };

type Status = { playing: boolean; queue_count: number; now_playing: { title: string } | null };

type Statuses = Record<string, Status | null>;

export default function DashboardPage() {
  const { account, loading, error } = useAccount();
  // Hydrate from the last fetched values so no fake default (0, "idle")
  // ever shows while the fresh data travels; "…" placeholders until then.
  const [guilds, setGuilds] = useState<Guild[] | null>(() => readCache<Guild[]>("pakodj:guilds", 10 * 60_000));
  const [statuses, setStatuses] = useState<Statuses | null>(() => readCache<Statuses>("pakodj:guild-statuses", 20_000));

  useEffect(() => {
    // Accounts without the view permission never read guild data; they get
    // the access-request onboarding screen instead (nothing to fetch).
    if (!account?.permissions.includes("view")) return;
    let alive = true;
    apiFetch<Guild[]>("/api/guilds")
      .then(async (gs) => {
        if (!alive) return;
        setGuilds(gs);
        writeCache("pakodj:guilds", gs);
        // One atomic statuses update (no progressive jump of the counters).
        const entries = await Promise.all(
          gs.map(async (g) => {
            const s = await apiFetch<Status>(`/api/guilds/${g.id}/status`).catch(() => null);
            return [g.id, s] as const;
          })
        );
        if (!alive) return;
        const map: Statuses = Object.fromEntries(entries);
        setStatuses(map);
        writeCache("pakodj:guild-statuses", map);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [account]);

  if (loading || !account) return error ? <ErrorBox message={error} /> : null;
  if (!account.permissions.includes("view")) return <RequestAccessPanel account={account} />;

  const playing = statuses
    ? Object.values(statuses).filter((s) => s?.playing).length
    : null;
  const queued = statuses
    ? Object.values(statuses).reduce((sum, s) => sum + (s?.queue_count ?? 0), 0)
    : null;

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <img src="/bot.png" alt="PakoDJ" className="w-10 h-10 rounded-xl" />
          <div>
            <h1 className="text-lg font-semibold">PakoDJ</h1>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-muted">
            {account.avatar_url && (
              <img src={account.avatar_url} alt="" className="w-6 h-6 rounded-full" />
            )}
            <span className="hidden sm:inline">{account.username}</span>
          </div>
          {account.permissions.includes("manage_users") && (
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
          <p className="text-2xl font-bold">{guilds === null ? "…" : guilds.length}</p>
        </div>
        <div className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted mb-1">Playing</p>
          <p className="text-2xl font-bold text-accent">{playing === null ? "…" : playing}</p>
        </div>
        <div className="bg-card border border-border rounded-xl p-4">
          <p className="text-xs text-muted mb-1">Queued</p>
          <p className="text-2xl font-bold">{queued === null ? "…" : queued}</p>
        </div>
      </div>

      {guilds !== null && guilds.length === 0 && (
        <p className="text-sm text-muted">No shared servers with the bot.</p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(guilds ?? []).map((g) => {
          const s = statuses?.[g.id]; // undefined = still loading, null = unreachable
          return (
            <Link
              key={g.id}
              to={`/guilds/${g.id}`}
              state={{ name: g.name }}
              className="bg-card border border-border rounded-xl p-4 hover:border-accent/60 transition"
            >
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium truncate">{g.name}</p>
                <span
                  className={`w-2 h-2 rounded-full ${
                    s === undefined ? "bg-muted" : s?.playing ? "bg-accent" : "bg-muted"
                  }`}
                />
              </div>
              <p className="text-xs text-secondary truncate">
                {s === undefined
                  ? "…"
                  : s?.now_playing?.title ?? (s === null ? "unreachable" : "idle")}
              </p>
              <p className="text-xs text-muted mt-1">
                {s === undefined ? "" : `${s?.queue_count ?? 0} in queue`}
              </p>
            </Link>
          );
        })}
      </div>
    </main>
  );
}
