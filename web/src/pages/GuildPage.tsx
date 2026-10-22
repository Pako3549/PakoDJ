import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { apiFetch } from "@/lib/api";
import { useAccount, usePermissionWatch } from "@/lib/auth";
import { useEscapeBack } from "@/lib/useEscapeBack";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type QueueItem = {
  id: number;
  title: string;
  webpage_url: string;
  position: number;
};

type GuildStatus = {
  guild_id: string;
  guild_name: string;
  voice_connected: boolean;
  playing: boolean;
  paused: boolean;
  now_playing: { title: string; webpage_url: string } | null;
  queue_count: number;
  loading_in_background: boolean;
};

type SearchHit = {
  id: number;
  title: string;
  artist: string;
  album: string;
  cover: string;
  preview: string;
  duration: number;
};

export default function GuildPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const location = useLocation();
  const { account, loading, error: authError } = useAccount();
  usePermissionWatch(account);
  // Granular gating: each control group maps to its own permission key,
  // resolved against the global set (the API applies per-guild overrides).
  const canPlayback = account?.permissions.includes("playback") ?? false;
  const canQueue = account?.permissions.includes("queue") ?? false;
  const canQueueManage = account?.permissions.includes("queue_manage") ?? false;
  // The header has a back arrow: Escape goes back to the dashboard.
  useEscapeBack(Boolean(guildId));

  // The guild name renders from the very first frame: it comes from the
  // dashboard link state (same-session navigation) or, on direct loads,
  // from the lightning-fast guilds list instead of the status endpoint.
  const [guildName, setGuildName] = useState<string>(
    (location.state as { name?: string } | null)?.name ?? ""
  );
  const [status, setStatus] = useState<GuildStatus | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [query, setQuery] = useState("");
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [previewing, setPreviewing] = useState<string | null>(null);

  const reloadData = useCallback(async () => {
    if (!guildId) return;
    try {
      const [s, q] = await Promise.all([
        apiFetch<GuildStatus>(`/api/guilds/${guildId}/status`),
        apiFetch<QueueItem[]>(`/api/guilds/${guildId}/queue`),
      ]);
      setStatus(s);
      setQueue(q);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, [guildId]);

  useEffect(() => {
    if (!account) return;
    if (guildId) {
      // Name refresh for direct URL loads / refreshes (no link state).
      apiFetch<{ id: string; name: string }[]>("/api/guilds")
        .then((gs) => {
          const g = gs.find((x) => x.id === guildId);
          if (g) setGuildName(g.name);
        })
        .catch(() => {});
    }
    reloadData();
  }, [account, guildId, reloadData]);

  // Tab title tracks the guild name; reset to the app title on unmount.
  useEffect(() => {
    if (guildName) document.title = `${guildName} · PakoDJ`;
    return () => {
      document.title = "PakoDJ";
    };
  }, [guildName]);

  useEffect(() => {
    setPreviewing(null);
  }, [query]);

  if (loading || !account || !guildId) return authError ? <ErrorBox message={authError} /> : null;

  const admin = async (path: string, init?: RequestInit) => {
    try {
      await apiFetch(path, init);
      await reloadData();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  // Optimistic batch reorder (drag & drop): reorder locally first, then let
  // the API confirm; on error reload from the source of truth.
  async function reorder(from: number, to: number) {
    if (from === to || !canQueueManage) return;
    const next = [...queue];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setQueue(next);
    setDragIndex(null);
    setOverIndex(null);
    try {
      await apiFetch(`/api/guilds/${guildId}/queue/reorder`, {
        method: "POST",
        body: JSON.stringify({ order: next.map((i) => i.id) }),
      });
      await reloadData();
    } catch (err) {
      setError((err as Error).message);
      await reloadData();
    }
  }

  // ---------- search + enqueue ----------
  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    try {
      const list = await apiFetch<SearchHit[]>(`/api/search?q=${encodeURIComponent(query)}`);
      setHits(list);
    } catch (err) {
      // no permission or metadata provider down: treat the raw text as a
      // direct play query instead (yt-dlp resolves "artist - title" too)
      setHits([]);
      await admin(`/api/guilds/${guildId}/play`, {
        method: "POST",
        body: JSON.stringify({ query: query.trim() }),
      });
      setQuery("");
      return;
    }
  }

  async function enqueueHit(hit: SearchHit, atEnd = true) {
    const q = `${hit.artist} - ${hit.title}`;
    await admin(atEnd ? `/api/guilds/${guildId}/queue` : `/api/guilds/${guildId}/play`, {
      method: "POST",
      body: JSON.stringify({ query: q }),
    });
    setQuery("");
    setHits([]);
    setPreviewing(null);
  }

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Link to="/" className="px-3 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition">
            ←
          </Link>
          <div>
            <h1 className="text-lg font-semibold">{guildName || "Guild"}</h1>
            <p className="text-xs text-muted truncate max-w-[420px]">
              {status?.now_playing?.title ?? "nothing playing"}
              {status?.paused ? " (paused)" : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-muted">
            {account.avatar_url && (
              <img src={account.avatar_url} alt="" className="w-6 h-6 rounded-full" />
            )}
            <span className="hidden sm:inline">{account.username}</span>
          </div>
          <span className={`w-2.5 h-2.5 rounded-full ${status?.playing ? "bg-accent" : "bg-muted"}`} />
          {(canPlayback || canQueue) && (
            <div className="flex items-center gap-2">
              {canPlayback && (status?.paused ? (
                <button onClick={() => admin(`/api/guilds/${guildId}/resume`, { method: "POST" })}
                  className="px-3 py-2 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Resume</button>
              ) : (
                <button onClick={() => admin(`/api/guilds/${guildId}/pause`, { method: "POST" })}
                  disabled={!status?.playing}
                  className="px-3 py-2 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50">Pause</button>
              ))}
              {canPlayback && (
                <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: false }) })}
                  className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition">Skip</button>
              )}
              {canPlayback && (
                <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: true }) })}
                  className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition">Skip all</button>
              )}
              {canPlayback && (
                <button onClick={() => admin(`/api/guilds/${guildId}/stop`, { method: "POST" })}
                  className="px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs font-medium hover:bg-red-500/20 transition">Stop</button>
              )}
            </div>
          )}
          <ThemeToggle />
        </div>
      </header>

      {error && <p className="text-xs text-red-400 mb-4">{error}</p>}

      {canQueue && (
        <section className="bg-card border border-border rounded-xl p-4 mb-6">
          <h2 className="text-sm font-medium mb-3">Add music</h2>
          <form onSubmit={runSearch} className="flex gap-2 mb-3">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={!canQueue}
              placeholder="Search a track or paste a YouTube / SoundCloud URL"
              className="flex-1 px-3 py-2 rounded-lg bg-surface border border-border text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent/60 disabled:opacity-50"
            />
            <button type="submit" disabled={!canQueue} className="px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition disabled:opacity-50">
              Search / Play
            </button>
          </form>
          {hits.length > 0 && (
            <div className="space-y-2">
              {hits.map((hit) => (
                <div key={hit.id} className="flex items-center gap-3 bg-surface border border-border rounded-lg p-2">
                  {hit.cover && <img src={hit.cover} alt="" className="w-10 h-10 rounded object-cover" />}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-primary truncate">{hit.title}</p>
                    <p className="text-xs text-muted truncate">{hit.artist} · {hit.album}</p>
                  </div>
                  <button onClick={() => setPreviewing(previewing === hit.preview ? null : hit.preview)}
                    className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition">
                    {previewing === hit.preview ? "Stop" : "Preview"}
                  </button>
                  {canQueue && (
                    <button onClick={() => enqueueHit(hit)}
                      className="px-3 py-1 rounded bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Queue</button>
                  )}
                  {canQueue && (
                    <button onClick={() => enqueueHit(hit, false)}
                      className="px-3 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition">Play now</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-medium">Queue</h2>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">{queue.length}</span>
              {canQueueManage && queue.length > 0 && (
                <button onClick={() => admin(`/api/guilds/${guildId}/queue`, { method: "DELETE" })}
                  className="px-2 py-1 rounded bg-red-500/10 text-red-400 text-xs hover:bg-red-500/20 transition">Clear</button>
              )}
            </div>
          </div>
          <div className="p-4 space-y-1 max-h-[420px] overflow-y-auto">
            {queue.length === 0 && <p className="text-xs text-muted">Queue is empty</p>}
            {queue.map((item, index) => (
              <div
                key={`${item.id}-${item.position}`}
                draggable={canQueueManage}
                onDragStart={() => setDragIndex(index)}
                onDragEnd={() => {
                  setDragIndex(null);
                  setOverIndex(null);
                }}
                onDragOver={(e) => {
                  if (dragIndex !== null) e.preventDefault();
                }}
                onDragEnter={() => {
                  if (dragIndex !== null && dragIndex !== index) setOverIndex(index);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  if (dragIndex !== null) reorder(dragIndex, index);
                  setDragIndex(null);
                  setOverIndex(null);
                }}
                className={`flex items-center gap-2 bg-surface border rounded-lg p-2 transition ${
                  dragIndex === index
                    ? "opacity-40 border-accent/60"
                    : overIndex === index
                      ? "border-accent/70 bg-accent/10"
                      : "border-border"
                }`}
              >
                {canQueueManage ? (
                  <span className="cursor-grab active:cursor-grabbing text-muted" title="Drag to reorder">
                    <svg viewBox="0 0 10 16" className="w-3 h-4" fill="currentColor" aria-hidden="true">
                      {[2, 6, 10, 14].map((y) =>
                        [3.5, 6.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.2" />)
                      )}
                    </svg>
                  </span>
                ) : (
                  <span className="w-3" />
                )}
                <span className="text-xs text-muted w-6 text-right">{index + 1}</span>
                <p className="flex-1 text-sm text-primary truncate">{item.title}</p>
                {canQueueManage && (
                  <button onClick={() => admin(`/api/guilds/${guildId}/queue/${item.id}`, { method: "DELETE" })}
                    className="px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition">✕</button>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <h2 className="text-sm font-medium">Info{status?.loading_in_background && " · loading playlist"}</h2>
          </div>
          <div className="p-4 space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted">Voice</span>
              <span>{status?.voice_connected ? "connected" : "not connected"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">State</span>
              <span>{status?.playing ? "playing" : status?.paused ? "paused" : "idle"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Queue length</span>
              <span>{status?.queue_count ?? 0}</span>
            </div>
          </div>
          {previewing && (
            <div className="border-t border-border p-4">
              <audio src={previewing} controls autoPlay className="w-full" />
              <p className="text-xs text-muted mt-1">30s preview from the metadata provider</p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
