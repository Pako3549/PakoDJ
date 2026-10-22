import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, clearToken, getToken } from "@/lib/api";
import type { Account } from "@/lib/auth";
import ErrorBox from "@/components/ErrorBox";

// Same avatar style as the users list: URL or letter fallback.
function Avatar({ src, name, size = "w-9 h-9" }: { src: string | null; name: string; size?: string }) {
  if (src) return <img src={src} alt="" className={`${size} rounded-full`} />;
  return (
    <span
      className={`${size} rounded-full bg-accent/20 text-accent text-xs font-semibold uppercase flex items-center justify-center`}
    >
      {name.charAt(0)}
    </span>
  );
}

type SharedGuild = { id: string; name: string; icon?: string | null };

// Server icon with an initials fallback: a stable color per guild id plus
// the first letters of the name, close to how Discord renders iconless guilds.
function GuildIcon({ guild, className }: { guild: SharedGuild; className: string }) {
  if (guild.icon) {
    return <img src={guild.icon} alt="" className={`${className} rounded-full object-cover`} />;
  }
  const palette = ["#5865F2", "#57CF28", "#FEE75C", "#EB459E", "#ED4245", "#3498DB"];
  const color = palette[Number(guild.id) % palette.length];
  const initials = guild.name.slice(0, 2).toUpperCase();
  return (
    <span
      className={`${className} rounded-full flex items-center justify-center text-inverse text-xs font-semibold`}
      style={{ backgroundColor: color }}
    >
      {initials}
    </span>
  );
}

// Shown when the account has no `view` permission: onboarding screen with a
// Discord-style server picker, an optional note, and ONE send action. With a
// server selected the request targets that guild; with none it is generic.
export default function RequestAccessPanel({ account }: { account: Account }) {
  const navigate = useNavigate();
  // Rendered immediately (empty while loading): no "Loading..." flash.
  const [guilds, setGuilds] = useState<SharedGuild[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<SharedGuild | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [genericRequested, setGenericRequested] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  useEffect(() => {
    let alive = true;
    apiFetch<SharedGuild[]>("/api/access/guilds")
      .then((gs) => {
        if (!alive) return;
        setGuilds(gs);
        setLoaded(true);
      })
      .catch(() => alive && setLoaded(true));
    // Restore what is already pending (from the database) so a reload locks
    // the send button instead of offering a duplicate click.
    apiFetch<{ guild_ids: (number | string)[]; generic: boolean }>(
      "/api/access-requests/mine"
    )
      .then((mine) => {
        if (!alive) return;
        setRequested(new Set(mine.guild_ids.map(String)));
        setGenericRequested(mine.generic);
      })
      .catch(() => { });
    return () => {
      alive = false;
    };
  }, [account.id]);

  const markDone = (label: string) => {
    setInfo(label);
    setError("");
  };

  const request = useCallback(
    async (guild: SharedGuild) => {
      setBusy(true);
      try {
        await apiFetch("/api/access-requests", {
          method: "POST",
          body: JSON.stringify({ guild_id: guild.id, message: message.trim() }),
        });
        setRequested((prev) => new Set([...prev, guild.id]));
        setMessage("");
        markDone(`Request for ${guild.name} sent.`);
      } catch (err) {
        if (err instanceof Error && /pending/i.test(err.message)) {
          setRequested((prev) => new Set([...prev, guild.id]));
          markDone(`A request for ${guild.name} is already pending.`);
        } else {
          setError((err as Error).message);
        }
      } finally {
        setBusy(false);
      }
    },
    [message]
  );

  const requestGeneric = useCallback(async () => {
    setBusy(true);
    try {
      await apiFetch("/api/access-requests", {
        method: "POST",
        body: JSON.stringify({ message: message.trim() }),
      });
      setGenericRequested(true);
      setMessage("");
      markDone("Generic request sent.");
    } catch (err) {
      if (err instanceof Error && /pending/i.test(err.message)) {
        setGenericRequested(true);
        markDone("A generic request is already pending.");
      } else {
        setError((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }, [message]);

  // Locked while the request matching the current selection is already
  // pending; restored from the database on every load.
  const locked = selected ? requested.has(selected.id) : genericRequested;

  // Session end for accounts stuck here with no permissions yet: kills the
  // API session (best effort) and drops the local token.
  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch {
      // Session already dead or unreachable: clearing locally is enough.
    }
    clearToken();
    navigate("/login", { replace: true });
  }, [navigate]);

  const send = useCallback(() => {
    if (busy || locked || !loaded) return;
    if (guilds?.length && selected) request(selected);
    else requestGeneric();
  }, [busy, locked, loaded, guilds, selected, request, requestGeneric]);

  // Server push instead of polling: ONE held SSE connection (no request
  // loops). The API notifies "granted" when an admin resolves a request or
  // directly edits the account; the page reloads into the dashboard. A
  // scoped-override grant leaves /me untouched, hence a single fallback
  // check on the visibility-filtered guild list right before reloading.
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const ctrl = new AbortController();
    (async () => {
      const res = await fetch("/api/stream/updates", {
        headers: { Authorization: `Bearer ${token}` },
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return; // transient drop: reload restores the stream
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        if (parts.some((c) => c.startsWith("event: granted"))) {
          // A grant may be global (permissions non-empty) or scoped-only
          // (override on one guild, /me stays empty but the guild becomes
          // visible); either detail forces the dashboard reload.
          const me = await apiFetch<Account>("/api/auth/me").catch(() => null);
          const visibles = me && me.permissions.length === 0
            ? await apiFetch<{ id: string }[]>("/api/guilds").catch(() => [])
            : [];
          if (!me || me.permissions.length > 0 || visibles.length > 0) {
            window.location.reload();
          }
        }
      }
    })().catch(() => { /* connection replaced or aborted on unmount */ });
    return () => ctrl.abort();
  }, []);

  return (
    <main className="min-h-svh flex items-center justify-center px-4">
      <div className="w-full max-w-lg">
      <header className="flex items-center gap-3 mb-6">
        {/* The account's own avatar in the header, not the bot logo. */}
        <Avatar src={account.avatar_url} name={account.username} size="w-10 h-10" />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold truncate">{account.username}</h1>
          <p className="text-xs text-muted">
            Request access
          </p>
        </div>
        <button
          onClick={logout}
          className="px-2.5 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition"
        >
          Log out
        </button>
      </header>

      <p className="text-sm text-secondary mb-4">
        Your account has no permissions yet. Ask for the servers you need: an
        admin reviews the request and grants the right permissions.
      </p>

      {error && <ErrorBox message={error} />}
      {info && <p className="text-xs text-accent mb-3">{info}</p>}

      {(
        <>
          {/* 1. Server picker, always on screen (the list fills as it loads). */}
          {(
            <div className="relative mb-4">
              <button
                onClick={() => setMenuOpen((o) => !o)}
                aria-haspopup="listbox"
                aria-expanded={menuOpen}
                className="w-full flex items-center gap-3 bg-card border border-border rounded-lg px-3 py-2.5 hover:border-accent/50 transition text-left"
              >
                {selected ? (
                  <GuildIcon guild={selected} className="w-8 h-8" />
                ) : (
                  // Without a selection the picker advertises the generic path
                  // instead of an empty looking slot.
                  <span className="w-8 h-8 rounded-full bg-accent/15 text-accent flex items-center justify-center">
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M22 2L11 13" />
                      <path d="M22 2l-7 20-4-9-9-4 20-7z" />
                    </svg>
                  </span>
                )}
                <span
                  className={`flex-1 truncate text-sm ${selected ? "text-primary" : "text-muted"}`}
                >
                  {selected ? selected.name : "Generic request"}
                </span>
                <svg
                  viewBox="0 0 24 24"
                  className={`w-4 h-4 text-muted transition ${menuOpen ? "rotate-180" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>

              {menuOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                  <div
                    role="listbox"
                    className="absolute z-20 mt-1 w-full max-h-56 overflow-y-auto bg-card border border-border rounded-lg shadow-xl py-1"
                  >
                    {!loaded ? (
                      <div className="px-3 py-2 space-y-2" aria-busy="true">
                        {[0, 1].map((i) => (
                          <div key={i} className="flex items-center gap-3">
                            <span className="w-8 h-8 rounded-full bg-surface animate-pulse" />
                            <span className="h-3 w-36 rounded bg-surface animate-pulse" />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <>
                        {/* First entry: the generic path, always available. */}
                        <button
                          role="option"
                          aria-selected={selected === null}
                          onClick={() => {
                            setSelected(null);
                            setMenuOpen(false);
                          }}
                          className="w-full flex items-center gap-3 px-3 py-2 hover:bg-surface transition text-left"
                        >
                          <span className="w-8 h-8 rounded-full bg-accent/15 text-accent flex items-center justify-center">
                            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                              <path d="M22 2L11 13" />
                              <path d="M22 2l-7 20-4-9-9-4 20-7z" />
                            </svg>
                          </span>
                          <span className="flex-1 truncate text-sm text-primary">Generic request</span>
                          {genericRequested && (
                            <span className="text-[10px] text-muted border border-border rounded px-1.5 py-0.5">
                              requested
                            </span>
                          )}
                        </button>
                        {guilds.length === 0 ? (
                          <p className="px-3 py-2 text-xs text-muted">
                            You share no server with the bot.
                          </p>
                        ) : guilds.map((g) => (
                          <button
                            key={g.id}
                            role="option"
                            aria-selected={selected?.id === g.id}
                            onClick={() => {
                              setSelected(g);
                              setMenuOpen(false);
                            }}
                            className="w-full flex items-center gap-3 px-3 py-2 hover:bg-surface transition text-left"
                          >
                            <GuildIcon guild={g} className="w-8 h-8" />
                            <span className="flex-1 truncate text-sm text-primary">{g.name}</span>
                            {requested.has(g.id) && (
                              <span className="text-[10px] text-muted border border-border rounded px-1.5 py-0.5">
                                requested
                              </span>
                            )}
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {loaded && guilds.length === 0 && (
            <p className="text-xs text-muted mb-4">
              You share no server with the bot yet, so the request below is
              generic: an admin will review it and contact you on Discord.
            </p>
          )}

          {/* 2. Optional note for the reviewing admins. */}
          <label className="block mb-4 text-xs text-muted">
            Optional note for the admins
            <input
              type="text"
              value={message}
              maxLength={200}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="e.g. I use it for the Friday evening sessions"
              className="mt-1 w-full px-3 py-2 rounded-lg bg-surface border border-border text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent/60"
            />
          </label>

          {/* 3. One send action: guild-scoped with a selection, generic without. */}
          {locked ? (
            <div className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-surface border border-border text-xs text-muted">
              <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="5" y="11" width="14" height="9" rx="2" />
                <path d="M8 11V8a4 4 0 0 1 8 0v3" />
              </svg>
              Request already pending — waiting for an admin review
            </div>
          ) : (
            <button
              onClick={send}
              disabled={busy || !loaded}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition disabled:opacity-50"
            >
              <span>Send request</span>
              <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M22 2L11 13" />
                <path d="M22 2l-7 20-4-9-9-4 20-7z" />
              </svg>
            </button>
          )}
        </>
      )}
      </div>
    </main>
  );
}
