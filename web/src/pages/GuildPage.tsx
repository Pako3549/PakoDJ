import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { apiFetch, clearToken } from "@/lib/api";
import { useAccount, usePermissionWatch } from "@/lib/auth";
import { useStreamEvents } from "@/lib/live";
import { goBack, useEscapeBack } from "@/lib/useEscapeBack";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type QueueItem = {
  id: number;
  title: string;
  webpage_url: string;
  position: number;
  search_query?: string | null;
  pending?: boolean;
};

type GuildStatus = {
  guild_id: string;
  guild_name: string;
  voice_connected: boolean;
  playing: boolean;
  paused: boolean;
  now_playing: { title: string; webpage_url: string; duration?: number | null } | null;
  position: number | null;
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
  link?: string | null;
};

type HistoryItem = {
  id: number;
  title: string;
  webpage_url: string;
  played_at: string;
};

type Favorite = {
  id: number;
  title: string;
  webpage_url: string;
  search_query?: string | null;
  created_at: string;
};

const QUEUE_PAGE_SIZE = 15;
const HISTORY_PAGE_SIZE = 20;
const FAV_PAGE_SIZE = 10;

// Seconds -> m:ss (or h:mm:ss past the hour), no fake zeros anywhere.
function fmt(total: number | null | undefined): string {
  if (total === null || total === undefined || total < 0) return "--:--";
  const s = Math.floor(total);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

// ---------- inline icons (consistent stroke style, no icon library) ----------
const iconCls = "w-4 h-4";

function ChevronIcon({ dir }: { dir: "left" | "right" }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={iconCls} aria-hidden="true">
      {dir === "left" ? <polyline points="15 18 9 12 15 6" /> : <polyline points="9 18 15 12 9 6" />}
    </svg>
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={iconCls} aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function GripIcon() {
  return (
    <svg viewBox="0 0 10 16" className="w-3 h-4" fill="currentColor" aria-hidden="true">
      {[3, 8, 13].map((y) => [3.5, 6.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.3" />))}
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={iconCls} aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function ArrowLeftIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={iconCls} aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
      <polygon points="12 2.6 15 8.8 21.8 9.7 16.9 14.4 18.1 21.1 12 17.9 5.9 21.1 7.1 14.4 2.2 9.7 9 8.8" />
    </svg>
  );
}

export default function GuildPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { account, loading, error: authError } = useAccount();
  usePermissionWatch(account);
  // Granular gating: each control group maps to its own permission key,
  // resolved against the global set (the API applies per-guild overrides).
  const canPlayback = account?.permissions.includes("playback") ?? false;
  const canQueue = account?.permissions.includes("queue") ?? false;
  const canQueueManage = account?.permissions.includes("queue_manage") ?? false;
  // The header has a back arrow: Escape goes back to the dashboard
  // (suppressed while the add-music dialog owns the Escape key).
  const [addOpen, setAddOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement | null>(null);
  useEscapeBack(Boolean(guildId) && !addOpen);

  // The guild name renders from the very first frame: it comes from the
  // dashboard link state (same-session navigation) or, on direct loads,
  // from the lightning-fast guilds list instead of the status endpoint.
  const [guildName, setGuildName] = useState<string>(
    (location.state as { name?: string } | null)?.name ?? ""
  );
  const [status, setStatus] = useState<GuildStatus | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [queuePage, setQueuePage] = useState(0);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [query, setQuery] = useState("");
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [history, setHistory] = useState<{ items: HistoryItem[]; total: number } | null>(null);
  const [historyPage, setHistoryPage] = useState(0);
  const [favorites, setFavorites] = useState<Favorite[] | null>(null);
  const [favQuery, setFavQuery] = useState("");
  const [favPage, setFavPage] = useState(0);
  const [searching, setSearching] = useState(false);
  const [playCount, setPlayCount] = useState(1);
  // Progress baseline: the position reported by the last status fetch plus
  // when it was fetched; the 1s ticker interpolates between fetches.
  const [posSync, setPosSync] = useState<{ position: number | null; at: number }>({ position: null, at: 0 });
  const [nowTs, setNowTs] = useState(Date.now());

  const fetchHistory = useCallback(async (page: number) => {
    if (!guildId) return;
    try {
      const data = await apiFetch<{ items: HistoryItem[]; total: number }>(
        `/api/guilds/${guildId}/history?offset=${page * HISTORY_PAGE_SIZE}`
      );
      setHistory(data);
      setHistoryPage(page);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [guildId]);

  const reloadData = useCallback(async () => {
    if (!guildId) return;
    try {
      const [s, q] = await Promise.all([
        apiFetch<GuildStatus>(`/api/guilds/${guildId}/status`),
        apiFetch<QueueItem[]>(`/api/guilds/${guildId}/queue`),
      ]);
      setStatus(s);
      setQueue(q);
      setPosSync({ position: s.position, at: Date.now() });
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, [guildId]);

  const fetchFavorites = useCallback(async () => {
    try {
      setFavorites(await apiFetch<Favorite[]>("/api/favorites"));
    } catch {
      // personal data: a failure must not break the music page
      setFavorites((f) => f ?? []);
    }
  }, []);

  // Star/unstar from queue or history: optimistic on the local list, the
  // server is the source of truth on the next fetch.
  const toggleFavorite = useCallback(async (title: string, webpageUrl: string, searchQuery?: string | null) => {
    const current = favorites ?? [];
    const existing = current.find((f) => f.webpage_url === webpageUrl);
    if (existing) {
      setFavorites(current.filter((f) => f.id !== existing.id));
      try {
        await apiFetch(`/api/favorites/${existing.id}`, { method: "DELETE" });
      } catch {
        await fetchFavorites();
      }
    } else {
      try {
        const res = await apiFetch<{ favorite_id: number }>("/api/favorites", {
          method: "POST",
          body: JSON.stringify({ title, webpage_url: webpageUrl, search_query: searchQuery ?? null }),
        });
        setFavorites([
          { id: res.favorite_id, title, webpage_url: webpageUrl, search_query: searchQuery ?? null, created_at: new Date().toISOString() },
          ...current,
        ]);
      } catch (err) {
        setError((err as Error).message);
        await fetchFavorites();
      }
    }
  }, [favorites, fetchFavorites]);

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
    fetchHistory(0);
    fetchFavorites();
  }, [account, guildId, reloadData, fetchHistory, fetchFavorites]);

  // Live playback push: the bot signals every state change (track start,
  // stop, pause, skip) on the shared SSE stream — no polling loops.
  useStreamEvents((name) => {
    if (name === "playback") {
      reloadData();
      fetchHistory(historyPage);
    }
  });

  // Add-music modal: focus the search box on open, Escape closes the
  // dialog itself (the page-level Escape-back is suppressed while open).
  useEffect(() => {
    if (!addOpen) return;
    searchRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAddOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [addOpen]);

  // 1s interpolation tick for the progress line (local only, no network).
  useEffect(() => {
    const id = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

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
  // Optimistic enqueue: the track shows up in the list right away with the
  // raw query as placeholder; the server confirmation (which resolves the
  // canonical data via yt-dlp) reconciles the truth through the SSE reload.
  function optimisticEnqueue(label: string, fire: () => Promise<void>, count = 1) {
    setQuery("");
    setHits([]);
    setPreviewing(null);
    setQueue((cur) => [
      ...cur,
      ...Array.from({ length: count }, (_, i) => ({
        id: -Date.now() - i,
        title: label,
        webpage_url: "",
        position: cur.length + i + 1,
        pending: true,
      })),
    ]);
    (async () => {
      try {
        await fire();
      } finally {
        setQueue((cur) => cur.filter((i) => !i.pending));
        await reloadData();
      }
    })();
  }

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    const text = query.trim();
    if (!text || searching) return;
    // Links skip the metadata search entirely: yt-dlp resolves the URL
    // directly (play now; the server queues it automatically when busy).
    if (/^https?:\/\//i.test(text)) {
      const count = playCount;
      optimisticEnqueue(text, async () => {
        for (let i = 0; i < count; i++) {
          await admin(`/api/guilds/${guildId}/play`, {
            method: "POST",
            body: JSON.stringify({ query: text }),
          });
        }
      }, count);
      return;
    }
    setSearching(true);
    try {
      const list = await apiFetch<SearchHit[]>(`/api/search?q=${encodeURIComponent(text)}`);
      setHits(list);
    } catch (err) {
      // no permission or metadata provider down: treat the raw text as a
      // direct play query instead (yt-dlp resolves "artist - title" too)
      setHits([]);
      optimisticEnqueue(text, () =>
        admin(`/api/guilds/${guildId}/play`, {
          method: "POST",
          body: JSON.stringify({ query: text }),
        })
      );
    } finally {
      setSearching(false);
    }
  }

  async function enqueueHit(hit: SearchHit, where: "end" | "next" = "end") {
    const q = `${hit.artist} - ${hit.title}`;
    const count = Math.max(1, Math.min(10, playCount || 1));
    optimisticEnqueue(q, async () => {
      for (let i = 0; i < count; i++) {
        await admin(`/api/guilds/${guildId}/queue`, {
          method: "POST",
          body: JSON.stringify(where === "next" ? { query: q, position: i } : { query: q }),
        });
      }
    }, count);
  }

  // ---------- progress interpolation ----------
  const np = status?.now_playing ?? null;
  const elapsed = status?.playing
    ? (posSync.position ?? 0) + Math.max(0, (nowTs - posSync.at) / 1000)
    : status?.paused
      ? (posSync.position ?? 0)
      : 0;
  const duration = np?.duration ?? null;
  const pct = duration ? Math.min(100, Math.max(0, (elapsed / duration) * 100)) : 0;

  // ---------- queue pagination ----------
  const qPages = Math.max(1, Math.ceil(queue.length / QUEUE_PAGE_SIZE));
  const qSafe = Math.min(queuePage, qPages - 1);
  const qStart = qSafe * QUEUE_PAGE_SIZE;
  const qItems = queue.slice(qStart, qStart + QUEUE_PAGE_SIZE);
  const hPages = Math.max(1, Math.ceil((history?.total ?? 0) / HISTORY_PAGE_SIZE));
  const hSafe = Math.min(historyPage, hPages - 1);
  const active = Boolean(status?.playing || status?.paused);

  // ---------- favorites ----------
  const favUrls = new Set((favorites ?? []).map((f) => f.webpage_url));
  const needle = favQuery.trim().toLowerCase();
  const favFiltered = (favorites ?? []).filter((f) =>
    !needle || (f.title + " " + f.webpage_url).toLowerCase().includes(needle));
  const favPages = Math.max(1, Math.ceil(favFiltered.length / FAV_PAGE_SIZE));
  const fSafe = Math.min(favPage, favPages - 1);
  const favPageItems = favFiltered.slice(fSafe * FAV_PAGE_SIZE, (fSafe + 1) * FAV_PAGE_SIZE);
  const playFavorite = (fav: Favorite) =>
    admin(`/api/guilds/${guildId}/queue`, {
      method: "POST",
      body: JSON.stringify({ query: fav.search_query || fav.webpage_url, position: 0 }),
    });

  const controlCls = "px-4 py-2 rounded-lg text-xs font-medium transition disabled:opacity-40";

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(goBack(location.pathname + location.search))} title="Back"
            className="p-2.5 rounded-lg bg-card border border-border text-secondary hover:text-primary transition">
            <ArrowLeftIcon />
          </button>
          <h1 className="text-lg font-semibold">{guildName || "Guild"}</h1>
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
            className="px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-400 hover:bg-red-500/20 transition"
          >
            Logout
          </button>
          <ThemeToggle />
        </div>
      </header>

      {error && <p className="text-xs text-red-400 mb-4">{error}</p>}

      {canQueue && (
        <div className="flex justify-end mb-4">
          <button
            onClick={() => setAddOpen(true)}
            className="px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition flex items-center gap-1.5"
          >
            <PlusIcon />
            Add music
          </button>
        </div>
      )}

      {/* Now playing: centered title, progress line, centered controls */}
      <section className="bg-card border border-border rounded-xl p-6 mb-6">
        <div className="text-center min-h-[3rem] flex flex-col justify-center">
          {np ? (
            <a href={np.webpage_url} target="_blank" rel="noreferrer" className="text-base font-medium text-primary hover:text-accent transition truncate px-4">
              {np.title}
            </a>
          ) : (
            <p className="text-sm text-muted">Nothing playing</p>
          )}
          {np && (
            <p className="text-xs text-muted mt-1">
              {status?.playing ? "Playing" : status?.paused ? "Paused" : ""}
              {status?.loading_in_background ? " · loading playlist" : ""}
            </p>
          )}
        </div>

        {np && (
          <div className="mt-4">
            {duration !== null && duration !== undefined ? (
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted w-12 text-right tabular-nums">{fmt(elapsed)}</span>
                <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
                  <div className="h-full bg-accent rounded-full transition-[width] duration-1000 ease-linear" style={{ width: `${pct}%` }} />
                </div>
                <span className="text-xs text-muted w-12 tabular-nums">{fmt(duration)}</span>
              </div>
            ) : (
              <p className="text-center text-xs text-muted tabular-nums">{fmt(elapsed)}</p>
            )}
          </div>
        )}

        <div className="flex items-center justify-center gap-2 mt-5 flex-wrap">
          {canPlayback && (
            status?.playing ? (
              <button onClick={() => admin(`/api/guilds/${guildId}/pause`, { method: "POST" })}
                className={`${controlCls} bg-accent text-inverse hover:bg-accent/90`}>Pause</button>
            ) : (
              <button onClick={() => admin(status?.paused ? `/api/guilds/${guildId}/resume` : `/api/guilds/${guildId}/queue/start`, { method: "POST" })}
                disabled={!status?.paused && !(status?.queue_count ?? 0)}
                title={!status?.paused && !(status?.queue_count ?? 0) ? "Nothing to play" : undefined}
                className={`${controlCls} bg-accent text-inverse hover:bg-accent/90`}>Play</button>
            )
          )}
          {canPlayback && (
            <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: false }) })}
              disabled={!active}
              className={`${controlCls} bg-card border border-border text-secondary hover:text-primary`}>Skip</button>
          )}
          {canPlayback && (
            <button onClick={() => admin(`/api/guilds/${guildId}/skip`, { method: "POST", body: JSON.stringify({ all: true }) })}
              disabled={!active}
              className={`${controlCls} bg-card border border-border text-secondary hover:text-primary`}>Skip all</button>
          )}
          {canPlayback && (
            <button onClick={() => admin(`/api/guilds/${guildId}/stop`, { method: "POST" })}
              disabled={!status?.voice_connected}
              className={`${controlCls} bg-red-500/10 text-red-400 hover:bg-red-500/20`}>Stop</button>
          )}
        </div>

        <p className="text-center text-xs text-muted mt-4">
          {status?.voice_connected ? "Voice connected" : "Voice not connected"}
          {" · "}
          {status?.queue_count ?? 0} in queue
        </p>
      </section>

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
          <div className="p-4 space-y-1 h-96 overflow-y-auto">
            {queue.length === 0 && <p className="text-xs text-muted">Queue is empty</p>}
            {qItems.map((item, index) => {
              const globalIndex = qStart + index;
              return (
                <div
                  key={`${item.id}-${item.position}`}
                  draggable={canQueueManage && !item.pending}
                  onDragStart={() => setDragIndex(globalIndex)}
                  onDragEnd={() => {
                    setDragIndex(null);
                    setOverIndex(null);
                  }}
                  onDragOver={(e) => {
                    if (dragIndex !== null) e.preventDefault();
                  }}
                  onDragEnter={() => {
                    if (dragIndex !== null && dragIndex !== globalIndex) setOverIndex(globalIndex);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragIndex !== null) reorder(dragIndex, globalIndex);
                    setDragIndex(null);
                    setOverIndex(null);
                  }}
                  className={`flex items-center gap-2 bg-surface border rounded-lg p-2 transition ${
                    dragIndex === globalIndex
                      ? "opacity-40 border-accent/60"
                      : overIndex === globalIndex
                        ? "border-accent/70 bg-accent/10"
                        : "border-border"
                  }`}
                >
                  {canQueueManage ? (
                    <span className="cursor-grab active:cursor-grabbing text-muted" title="Drag to reorder">
                      <GripIcon />
                    </span>
                  ) : (
                    <span className="w-3" />
                  )}
                  <span className="text-xs text-muted w-6 text-right">{globalIndex + 1}</span>
                  <p className="flex-1 text-sm truncate">
                    <span className={item.pending ? "text-muted" : "text-primary"}>{item.title}</span>
                    {item.pending && <span className="text-xs text-muted"> · adding…</span>}
                  </p>
                  {!item.pending && (
                    <button
                      onClick={() => toggleFavorite(item.title, item.webpage_url, item.search_query)}
                      title={favUrls.has(item.webpage_url) ? "Remove from favorites" : "Add to favorites"}
                      className={`p-1.5 rounded transition ${
                        favUrls.has(item.webpage_url) ? "text-amber-400" : "text-muted hover:text-secondary"
                      }`}
                    >
                      <StarIcon filled={favUrls.has(item.webpage_url)} />
                    </button>
                  )}
                  {canQueueManage && !item.pending && (
                    <button onClick={() => admin(`/api/guilds/${guildId}/queue/${item.id}`, { method: "DELETE" })}
                      title="Remove from queue"
                      className="p-1.5 rounded-md text-muted opacity-60 hover:opacity-100 hover:text-red-400 hover:bg-red-500/10 transition"><XIcon /></button>
                  )}
                </div>
              );
            })}
          </div>
          {qPages > 1 && (
            <div className="px-4 py-3 border-t border-border flex items-center justify-center gap-3 text-xs text-muted">
              <button onClick={() => setQueuePage(qSafe - 1)} disabled={qSafe === 0} title="Previous page"
                className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="left" /></button>
              <span>{qSafe + 1} / {qPages}</span>
              <button onClick={() => setQueuePage(qSafe + 1)} disabled={qSafe >= qPages - 1} title="Next page"
                className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="right" /></button>
            </div>
          )}
        </section>

        <section className="bg-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-medium">History</h2>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">{history?.total ?? 0}</span>
              {canQueueManage && (history?.total ?? 0) > 0 && (
                <button onClick={async () => { await admin(`/api/guilds/${guildId}/history`, { method: "DELETE" }); await fetchHistory(0); }}
                  className="px-2 py-1 rounded bg-red-500/10 text-red-400 text-xs hover:bg-red-500/20 transition">Clear</button>
              )}
            </div>
          </div>
          <div className="p-4 space-y-1 h-96 overflow-y-auto">
            {(history?.items.length ?? 0) === 0 && <p className="text-xs text-muted">Nothing played yet</p>}
            {history?.items.map((item) => (
              <div key={item.id} className="flex items-center gap-2 bg-surface border border-border rounded-lg p-2">
                <p className="flex-1 text-sm text-primary truncate">{item.title}</p>
                <span className="text-xs text-muted whitespace-nowrap">
                  {new Date(item.played_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}
                </span>
                <button
                  onClick={() => toggleFavorite(item.title, item.webpage_url)}
                  title={favUrls.has(item.webpage_url) ? "Remove from favorites" : "Add to favorites"}
                  className={`p-1.5 rounded transition ${
                    favUrls.has(item.webpage_url) ? "text-amber-400" : "text-muted hover:text-secondary"
                  }`}
                >
                  <StarIcon filled={favUrls.has(item.webpage_url)} />
                </button>
              </div>
            ))}
          </div>
          {hPages > 1 && (
            <div className="px-4 py-3 border-t border-border flex items-center justify-center gap-3 text-xs text-muted">
              <button onClick={() => fetchHistory(hSafe - 1)} disabled={hSafe === 0} title="Previous page"
                className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="left" /></button>
              <span>{hSafe + 1} / {hPages}</span>
              <button onClick={() => fetchHistory(hSafe + 1)} disabled={hSafe >= hPages - 1} title="Next page"
                className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="right" /></button>
            </div>
          )}
        </section>
      </div>

      <section className="bg-card border border-border rounded-xl overflow-hidden mt-4">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-sm font-medium">Favorites</h2>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={favQuery}
              onChange={(e) => setFavQuery(e.target.value)}
              placeholder="Search favorites"
              className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs text-primary placeholder:text-muted focus:outline-none focus:border-accent/60"
            />
            <span className="text-xs text-muted">{favorites?.length ?? 0}</span>
          </div>
        </div>
        <div className="p-4 min-h-[8rem]">
          {favFiltered.length === 0 && (
            <p className="text-xs text-muted">
              {favQuery.trim() ? "No favorite matches the search" : "Star a track in the queue or the history to keep it here"}
            </p>
          )}
          <div className="grid md:grid-cols-2 gap-x-4 gap-y-1">
            {favPageItems.map((fav) => (
              <div key={fav.id} className="flex items-center gap-2 bg-surface border border-border rounded-lg p-2">
                <button
                  onClick={() => toggleFavorite(fav.title, fav.webpage_url, fav.search_query)}
                  title="Remove from favorites"
                  className="text-amber-400 flex-none hover:text-muted transition"
                >
                  <StarIcon filled />
                </button>
                <p className="flex-1 text-sm text-primary truncate">{fav.title}</p>
                {canQueue && (
                  <button onClick={() => playFavorite(fav)} title="Play right after the current track"
                    className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition whitespace-nowrap">Play next</button>
                )}
                {canQueue && (
                  <button onClick={() => { playFavorite(fav); }}
                    className="px-2 py-1 rounded bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition">Queue</button>
                )}
              </div>
            ))}
          </div>
        </div>
        {favPages > 1 && (
          <div className="px-4 py-3 border-t border-border flex items-center justify-center gap-3 text-xs text-muted">
            <button onClick={() => setFavPage(fSafe - 1)} disabled={fSafe === 0} title="Previous page"
              className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="left" /></button>
            <span>{fSafe + 1} / {favPages}</span>
            <button onClick={() => setFavPage(fSafe + 1)} disabled={fSafe >= favPages - 1}
              className="p-1.5 rounded bg-card border border-border hover:text-primary transition disabled:opacity-40"><ChevronIcon dir="right" /></button>
          </div>
        )}
      </section>

      {/* Add-music dialog: search, results, previews; Escape/backdrop/X close.
          Enqueueing keeps the dialog open so several tracks can be added. */}
      {addOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-start sm:items-center justify-center p-4 overflow-y-auto"
          onClick={() => setAddOpen(false)}
        >
          <div
            className="bg-card border border-border rounded-2xl w-full max-w-xl shadow-2xl flex flex-col max-h-[85vh]"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 flex items-center gap-3 border-b border-border">
              <div className="w-9 h-9 rounded-lg bg-accent/15 text-accent grid place-items-center flex-none">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4.5 h-4.5" width="18" height="18" aria-hidden="true">
                  <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <h2 className="text-sm font-semibold">Add music</h2>
                <p className="text-xs text-muted">Search a track or paste a link</p>
              </div>
              <button onClick={() => setAddOpen(false)} title="Close"
                className="p-1.5 rounded-md text-muted hover:text-primary hover:bg-surface transition"><XIcon /></button>
            </div>

            <div className="px-5 py-4 border-b border-border">
              <form onSubmit={runSearch} className="flex gap-2">
                <input
                  ref={searchRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-lg bg-surface border border-border text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent/60"
                />
                <div
                  className="flex items-center gap-1 rounded-lg bg-surface border border-border px-2 py-1.5"
                  title="How many times to add the track (Play next keeps the order)"
                >
                  <span className="text-xs text-muted">×</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={playCount}
                    onChange={(e) => setPlayCount(Math.max(1, Math.min(10, parseInt(e.target.value) || 1)))}
                    className="w-9 bg-transparent text-xs text-primary text-center focus:outline-none"
                  />
                </div>
                <button type="submit" disabled={searching} className="px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition disabled:opacity-50">
                  {searching ? "Searching…" : "Search"}
                </button>
              </form>
              {searching && <p className="text-xs text-muted mt-2">Searching the metadata provider…</p>}
            </div>

            <div className="px-5 py-4 overflow-y-auto flex-1 space-y-2">
              {hits.length > 0 && (
                <p className="text-xs text-muted">Star a track, preview it, or send it to the queue.</p>
              )}
              {hits.map((hit) => {
                const favUrl = hit.link || `deezer:${hit.id}`;
                const q = `${hit.artist} - ${hit.title}`;
                return (
                  <div key={hit.id} className="flex items-center gap-3 bg-surface border border-border rounded-lg p-2">
                    {hit.cover && <img src={hit.cover} alt="" className="w-10 h-10 rounded object-cover flex-none" />}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-primary truncate">{hit.title}</p>
                      <p className="text-xs text-muted truncate">{hit.artist} · {hit.album}</p>
                    </div>
                    <button
                      onClick={() => toggleFavorite(q, favUrl, q)}
                      title={favUrls.has(favUrl) ? "Remove from favorites" : "Add to favorites"}
                      className={`p-1.5 rounded transition ${favUrls.has(favUrl) ? "text-amber-400" : "text-muted hover:text-secondary"}`}
                    >
                      <StarIcon filled={favUrls.has(favUrl)} />
                    </button>
                    <button onClick={() => setPreviewing(previewing === hit.preview ? null : hit.preview)}
                      className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition whitespace-nowrap">
                      {previewing === hit.preview ? "Stop" : "Preview"}
                    </button>
                    {canQueue && (
                      <button onClick={() => enqueueHit(hit, "next")}
                        className="px-2 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition whitespace-nowrap">Play next</button>
                    )}
                    {canQueue && (
                      <button onClick={() => enqueueHit(hit, "end")}
                        className="px-2 py-1 rounded bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition whitespace-nowrap">Queue</button>
                    )}
                  </div>
                );
              })}
            </div>

            {previewing && (
              <div className="px-5 py-4 border-t border-border">
                <audio src={previewing} controls autoPlay className="w-full" />
                <p className="text-xs text-muted mt-1">30s preview from the metadata provider</p>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
