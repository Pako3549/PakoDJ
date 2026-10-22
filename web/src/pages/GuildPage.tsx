import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "@/lib/api";
import { useAccount } from "@/lib/auth";
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
  const { account, loading, error: authError } = useAccount();
  const isAdmin = account?.role === "admin";

  const [status, setStatus] = useState<GuildStatus | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [query, setQuery] = useState("");
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
    reloadData();
  }, [account, guildId, reloadData]);

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
            <h1 className="text-lg font-semibold">Guild {guildId}</h1>
            <p className="text-xs text-muted truncate max-w-[420px]">
              {status?.now_playing?.title ?? "nothing playing"}
              {status?.paused ? " (paused)" : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${status?.playing ? "bg-accent" : "bg-muted"}`} />
          {isAdmin && (
            <div className="flex items-center gap-2">
              {status?.paused ? (
                <button onClick={() => admin(`/api/guilds/${guildId}/resume`, { method: "POST" })}
                  className="px-3 py-2 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Resume</button>
              ) : (
                <button onClick={() => admin(`/api/guilds/${guildId}/pause`, { method: "POST" })}
                  className="px-3 py-2 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Pause</button>
              )}
              <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: false }) })}
                className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition">Skip</button>
              <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: true }) })}
                className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition">Skip all</button>
              <button onClick={() => admin(`/api/guilds/${guildId}/stop`, { method: "POST" })}
                className="px-3 py-2 rounded-lg bg-red-500/10 text-red-400 text-xs font-medium hover:bg-red-500/20 transition">Stop</button>
            </div>
          )}
          <ThemeToggle />
        </div>
      </header>

      {error && <p className="text-xs text-red-400 mb-4">{error}</p>}

      {isAdmin && (
        <section className="bg-card border border-border rounded-xl p-4 mb-6">
          <h2 className="text-sm font-medium mb-3">Add music</h2>
          <form onSubmit={runSearch} className="flex gap-2 mb-3">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a track or paste a YouTube / SoundCloud URL"
              className="flex-1 px-3 py-2 rounded-lg bg-surface border border-border text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent/60"
            />
            <button type="submit" className="px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition">
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
                  <button onClick={() => enqueueHit(hit)}
                    className="px-3 py-1 rounded bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Queue</button>
                  <button onClick={() => enqueueHit(hit, false)}
                    className="px-3 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition">Play now</button>
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
              {isAdmin && queue.length > 0 && (
                <button onClick={() => admin(`/api/guilds/${guildId}/queue`, { method: "DELETE" })}
                  className="px-2 py-1 rounded bg-red-500/10 text-red-400 text-xs hover:bg-red-500/20 transition">Clear</button>
              )}
            </div>
          </div>
          <div className="p-4 space-y-1 max-h-[420px] overflow-y-auto">
            {queue.length === 0 && <p className="text-xs text-muted">Queue is empty</p>}
            {queue.map((item, index) => (
              <div key={`${item.id}-${item.position}`} className="flex items-center gap-2 bg-surface border border-border rounded-lg p-2">
                <span className="text-xs text-muted w-6 text-right">{index + 1}</span>
                <p className="flex-1 text-sm text-primary truncate">{item.title}</p>
                {isAdmin && (
                  <div className="flex items-center gap-1">
                    {index > 0 && (
                      <button onClick={() => admin(`/api/guilds/${guildId}/queue/${item.id}`, { method: "PATCH", body: JSON.stringify({ position: item.position - 1 }) })}
                        className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition">↑</button>
                    )}
                    {index < queue.length - 1 && (
                      <button onClick={() => admin(`/api/guilds/${guildId}/queue/${item.id}`, { method: "PATCH", body: JSON.stringify({ position: item.position + 1 }) })}
                        className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition">↓</button>
                    )}
                    <button onClick={() => admin(`/api/guilds/${guildId}/queue/${item.id}`, { method: "DELETE" })}
                      className="px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition">✕</button>
                  </div>
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
