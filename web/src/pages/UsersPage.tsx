import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch, getToken } from "@/lib/api";
import { useAccount, PERMISSIONS } from "@/lib/auth";
import { goBack, useEscapeBack } from "@/lib/useEscapeBack";
import { readCache, writeCache } from "@/lib/cache";

// Per-guild overrides never carry manage_users: account administration is
// global-only, matching the narrower Literal of the guild override API.
const GUILD_PERMISSIONS = PERMISSIONS.filter((p) => p !== "manage_users");
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type User = {
  id: number;
  username: string;
  permissions: string[];
  seed: boolean;
  discord_id: string;
  avatar_url: string | null;
  guild_overrides: GuildOverride[];
  guild_ids: string;
};

type GuildOverride = { guild_id: string; permissions: string[] };

type UserDetail = User & { guild_overrides: GuildOverride[]; guild_ids: string };

type AccessRequest = {
  id: number;
  user_id: number;
  username: string;
  guild_id: number | null;
  guild_name: string;
  message: string;
  created_at: string;
};

type Guild = { id: string; name: string };

// Edited-but-not-saved local state; nothing is pushed until Save.
type RowStaged = { permissions: string[]; removed: boolean };

const same = (a: string[], b: string[]) => [...a].sort().join() === [...b].sort().join();

// Short human descriptions next to the toggles.
const PERMISSION_DESC: Record<string, string> = {
  view: "See servers, queues and now playing",
  queue: "Search and add tracks, play them",
  queue_manage: "Remove, reorder and clear the queue",
  playback: "Pause, resume, skip and stop",
  manage_users: "Manage accounts, permissions and requests",
};

function stagePerm(list: string[], perm: string, on: boolean): string[] {
  // 'view' is the entry permission: dropping it empties the whole set, and
  // no other chip can ever live in a viewless set (the API rejects 422).
  if (perm === "view" && !on) return [];
  const next = [...new Set(on ? [...list, perm] : list.filter((p) => p !== perm))];
  if (!next.includes("view") && next.length > 0) return list;
  return next;
}

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

function SeedBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-accent/30 text-accent"
      title="Managed by DISCORD_ADMIN_IDS; cannot be changed"
    >
      <svg viewBox="0 0 24 24" className="w-2.5 h-2.5" fill="currentColor" aria-hidden="true">
        <path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 8V7a3 3 0 1 1 6 0v3H9z" />
      </svg>
      seed
    </span>
  );
}

function Chip({
  active,
  onClick,
  disabled,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  label: string;
  hint?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={hint ?? PERMISSION_DESC[label]}
      className={`px-2.5 py-1 text-xs rounded-full border transition ${
        active
          ? "bg-accent/20 border-accent/40 text-accent font-medium"
          : "border-border text-muted hover:text-secondary hover:border-accent/30"
      } disabled:opacity-40`}
    >
      {label}
    </button>
  );
}

export default function UsersPage() {
  const { account, loading, error: authError } = useAccount("manage_users");
  const location = useLocation();
  const navigate = useNavigate();
  // Instant-render caches; "null" renders as placeholders, never fake zeros.
  const [users, setUsers] = useState<User[] | null>(() => readCache<User[]>("pakodj:users", 60_000));
  const [requests, setRequests] = useState<AccessRequest[] | null>(() =>
    readCache<AccessRequest[]>("pakodj:access-requests", 30_000)
  );
  const [guilds, setGuilds] = useState<Guild[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, UserDetail | "loading">>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  // Staged edits (keyed per account / per override row); cleared after Save.
  const [stagedGlobal, setStagedGlobal] = useState<Record<number, string[]>>({});
  const [stagedGuild, setStagedGuild] = useState<Record<string, RowStaged>>({});
  // New override picked from the select but not saved yet: also per account.
  const [pendingGuild, setPendingGuild] = useState<Record<number, string[]>>({});
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  // Accounts list controls: free search over username / discord id plus
  // client-side pagination (row universe is small and already cached). The
  // chosen page size persists in localStorage for the next visits.
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZES = [8, 16, 32, 64] as const;
  const [pageSize, setPageSize] = useState<number>(() => {
    const stored = Number(localStorage.getItem("pakodj:users-page-size"));
    return PAGE_SIZES.includes(stored as never) ? stored : 8;
  });

  // Modal keys: Escape unwinds the confirm popup first, then the modal. The
  // hooks MUST sit before the loading early-return below: conditional hooks
  // after a return are a render-order crash (blank page).
  useEscapeBack(openId === null && confirmDelete === null);
  useEffect(() => {
    if (confirmDelete !== null) {
      const onKey = (e: KeyboardEvent) => {
        if (e.key === "Escape") setConfirmDelete(null);
      };
      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    }
    if (openId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      closeEditor(openId);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, confirmDelete]);

  const reload = useCallback(async () => {
    try {
      const [us, rs, gs] = await Promise.all([
        apiFetch<User[]>("/api/users"),
        apiFetch<AccessRequest[]>("/api/access-requests"),
        apiFetch<Guild[]>("/api/guilds"),
      ]);
      setUsers(us);
      writeCache("pakodj:users", us);
      setRequests(rs);
      writeCache("pakodj:access-requests", rs);
      setGuilds(gs);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (account) reload();
  }, [account, reload]);

  // Admin queue live update (same no-polling approach as the request panel):
  // the API broadcasts "requests" to manage_users streams on every newly
  // created access request and this page reloads its data in place.
  useEffect(() => {
    const token = getToken();
    if (!account || !token) return;
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
        if (done) return; // drop: a reload restores the stream
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        if (parts.some((c) => c.startsWith("event: requests"))) reload();
      }
    })().catch(() => { /* stream replaced or aborted on unmount */ });
    return () => ctrl.abort();
  }, [account?.id, reload]);

  if (loading || !account) return authError ? <ErrorBox message={authError} /> : null;

  async function run(key: string, fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(key);
    setError("");
    try {
      await fn();
      const fresh = await apiFetch<User[]>("/api/users");
      setUsers(fresh);
      const editorId = openId;
      // Skip the detail refresh when the open account no longer exists
      // (just deleted with its editor open: the 404 must not surface).
      if (
        editorId !== null &&
        details[editorId] &&
        details[editorId] !== "loading" &&
        fresh.some((usr) => usr.id === editorId)
      ) {
        const freshDetail = await apiFetch<UserDetail>(`/api/users/${editorId}`);
        setDetails((d) => ({ ...d, [editorId]: freshDetail }));
      }
      setError("");
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setBusy(null);
    }
  }

  const openEditor = (u: User) => {
    setOpenId(u.id);
    // Always refresh the detail on open: the modal must show the CURRENT
    // grants, not a cached copy taken before a grant/delete of this session.
    // A clean "loading" hint avoids flashing the possibly stale copy.
    setDetails((d) => ({ ...d, [u.id]: "loading" }));
    apiFetch<UserDetail>(`/api/users/${u.id}`)
      .then((detail) => setDetails((d) => ({ ...d, [u.id]: detail })))
      .catch(() => setDetails((d) => ({ ...d, [u.id]: "loading" })));
  };

  // Wipe the unapplied staging of one account (Discard button / modal close).
  // The unsaved badge warns while staging is pending; this is the reset path.
  const clearStaging = (userId: number) => {
    setStagedGlobal((s) => { const c = { ...s }; delete c[userId]; return c; });
    setPendingGuild((s) => { const c = { ...s }; delete c[userId]; return c; });
    setStagedGuild((s) =>
      Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith(`${userId}:`)))
    );
  };

  const closeEditor = (userId: number) => {
    clearStaging(userId);
    setConfirmDelete(null);
    setOpenId(null);
  };

  const permsFor = (u: User) => stagedGlobal[u.id] ?? u.permissions;
  const globalDirty = (u: User) => u.id in stagedGlobal && !same(stagedGlobal[u.id], u.permissions);

  // Row subtitle mirrors the real access shape: list the global set, or keep
  // the scoped grants to one short line (details in the native tooltip).
  const subtitleFor = (u: User): { text: string; full?: string } => {
    const globalSet = permsFor(u);
    if (globalSet.length) return { text: globalSet.join(" · ") };
    const chunks = (u.guild_overrides ?? [])
      .filter((o) => o.permissions.length > 0)
      .map((o) => {
        const name = guilds.find((g) => g.id === String(o.guild_id))?.name ?? o.guild_id;
        return `${o.permissions.join(", ")} in ${name}`;
      });
    if (chunks.length) {
      return {
        text: `scoped: ${chunks.length} server${chunks.length === 1 ? "" : "s"}`,
        full: chunks.join(" · "),
      };
    }
    return { text: "no permissions yet" };
  };

  // Guilds the target account shares with the bot (snapshot from its last
  // login, stored as JSON in the users row); an empty snapshot means it is
  // in none of them.
  const sharedGuildIds = (u: User): string[] => {
    const d = details[u.id];
    if (!d || d === "loading") return [];
    try {
      const parsed = JSON.parse((d as { guild_ids?: string }).guild_ids ?? "[]");
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  };

  // Unified row view: an existing override and a pending (new) one share the
  // same staged shape; saved permissions are the fallback when untouched.
  const rowView = (u: User, guildId: string, saved?: GuildOverride): RowStaged => {
    const st = stagedGuild[`${u.id}:${guildId}`];
    if (!st) return { permissions: saved?.permissions ?? ["view"], removed: false };
    return {
      permissions: st.permissions,
      // A pending row can only be discarded, never "kept removed".
      removed: saved ? st.removed : false,
    };
  };

  // Whole-dialog dirtiness: it drives the bottom Save/Discard bar.
  const modalDirty = (u: User) => {
    if (globalDirty(u)) return true;
    const detailData = details[u.id];
    if (!detailData || detailData === "loading") return false;
    for (const o of detailData.guild_overrides) {
      const st = stagedGuild[`${u.id}:${o.guild_id}`];
      if (st && (st.removed || !same(st.permissions, o.permissions))) return true;
    }
    return (pendingGuild[u.id] ?? []).length > 0;
  };

  // Stage new per-guild override rows (one server or all remaining ones at
  // once); each starts from ["view"] and stays editable until the batch Save.
  const stagePending = (u: User, guildIds: string[]) => {
    const fresh = guildIds.filter((gid) => !(pendingGuild[u.id] ?? []).includes(gid));
    if (!fresh.length) return;
    setStagedGuild((s) => {
      const c = { ...s };
      for (const gid of fresh) {
        const key = `${u.id}:${gid}`;
        if (!c[key]) c[key] = { permissions: ["view"], removed: false };
      }
      return c;
    });
    setPendingGuild((s) => ({ ...s, [u.id]: [...new Set([...(s[u.id] ?? []), ...fresh])] }));
  };

  // One bottom Save commits the whole dialog in flight: global set (a single
  // PATCH) plus every row change (PUT per override, DELETE per removed one).
  // Staging is cleared only when every call succeeded.
  const saveAll = (u: User) => {
    if (!modalDirty(u)) return;
    const detailData = details[u.id];
    if (!detailData || detailData === "loading") return;
    const calls: Promise<unknown>[] = [];
    if (globalDirty(u)) {
      calls.push(
        apiFetch(`/api/users/${u.id}`, {
          method: "PATCH",
          body: JSON.stringify({ permissions: stagedGlobal[u.id] }),
        })
      );
    }
    for (const o of detailData.guild_overrides) {
      const st = stagedGuild[`${u.id}:${o.guild_id}`];
      if (!st) continue;
      if (st.removed) {
        calls.push(apiFetch(`/api/users/${u.id}/guilds/${o.guild_id}`, { method: "DELETE" }));
      } else if (!same(st.permissions, o.permissions)) {
        calls.push(
          apiFetch(`/api/users/${u.id}/guilds/${o.guild_id}`, {
            method: "PUT",
            // Defensive: guild overrides are global-less, manage_users never goes.
            body: JSON.stringify({
              permissions: st.permissions.filter((p) => p !== "manage_users"),
            }),
          })
        );
      }
    }
    for (const gid of pendingGuild[u.id] ?? []) {
      calls.push(
        apiFetch(`/api/users/${u.id}/guilds/${gid}`, {
          method: "PUT",
          body: JSON.stringify({
            permissions: rowView(u, gid).permissions.filter((p) => p !== "manage_users"),
          }),
        })
      );
    }
    if (!calls.length) return;
    run(`modal:${u.id}`, () => Promise.all(calls)).then((ok) => {
      if (ok) {
        // Success closes the editor: grants are visible in the list and
        // nothing stays staged.
        clearStaging(u.id);
        setOpenId((prev) => (prev === u.id ? null : prev));
      }
    });
  };

  // Resolving a request must immediately empty the queue row: the follow-up
  // reload pulls users + requests in one go (no manual page refresh).
  // A guild request grants `view` ONLY inside that guild (per-guild override,
  // the global set stays untouched); a generic request grants global view.
  const grantAccess = (userId: number, requestId: number, guildId: number | null) =>
    run(`global:${userId}`, () =>
      (guildId === null
        ? apiFetch(`/api/users/${userId}`, {
            method: "PATCH",
            body: JSON.stringify({ permissions: ["view"] }),
          })
        : apiFetch(`/api/users/${userId}/guilds/${guildId}`, {
            method: "PUT",
            body: JSON.stringify({ permissions: ["view"] }),
          })
      ).then(() =>
        apiFetch(`/api/access-requests/${requestId}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "granted" }),
        })
      )
    ).then((ok) => {
      if (ok) reload();
    });

  // Account removal: discrete two-step action (confirm button appears first).
  const deleteUser = (userId: number) =>
    run(`delete:${userId}`, () => apiFetch(`/api/users/${userId}`, { method: "DELETE" })).then(() => {
      setConfirmDelete(null);
      setOpenId((prev) => (prev === userId ? null : prev));
    });

  const denyRequest = (requestId: number) =>
    run(`deny:${requestId}`, () =>
      apiFetch(`/api/access-requests/${requestId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "denied" }),
      })
    ).then((ok) => {
      if (ok) reload();
    });

  const myId = account.id;
  const avatarByUserId = new Map((users ?? []).map((u) => [u.id, u.avatar_url]));

  // Search + pagination over the loaded list: no server-side paging needed
  // while the whole list is already in cache and small; the filter is instant.
  const query = search.trim().toLowerCase();
  const filteredUsers = (users ?? []).filter(
    (u) =>
      !query ||
      u.username.toLowerCase().includes(query) ||
      u.discord_id.includes(query)
  );
  const totalPages = Math.max(1, Math.ceil(filteredUsers.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = filteredUsers.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <main className="max-w-3xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(goBack(location.pathname))} title="Back"
            className="px-3 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition">
            ←
          </button>
          <div>
            <h1 className="text-lg font-semibold">Users</h1>
            <p className="text-xs text-muted">
              {users === null
                ? "loading..."
                : `${users.length} account${users.length === 1 ? "" : "s"}${
                    requests?.length
                      ? ` · ${requests.length} pending request${requests.length === 1 ? "" : "s"}`
                      : ""
                  }`}
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
          <ThemeToggle />
        </div>
      </header>

      {error && <ErrorBox message={error} />}

      {requests !== null && requests.length > 0 && (
        <section className="bg-card border border-border rounded-xl mb-6">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h2 className="text-sm font-medium text-accent">Access requests</h2>
            <span className="text-xs text-muted">{requests.length}</span>
          </div>
          <div className="p-4 space-y-2">
            {requests.map((r) => (
              <div key={r.id} className="flex items-center gap-3 bg-surface border border-border rounded-lg p-3">
                <Avatar src={avatarByUserId.get(r.user_id) ?? null} name={r.username} size="w-8 h-8" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-primary truncate">{r.username}</p>
                  <p className="text-xs text-secondary truncate">
                    {r.guild_id === null ? (
                      <>requests <span className="text-muted">generic access (no shared server)</span></>
                    ) : (
                      <>
                        requests access to <span className="text-muted">{r.guild_name || r.guild_id}</span>
                      </>
                    )}
                  </p>
                  {r.message && (
                    <p className="mt-1.5 pl-2.5 border-l-2 border-accent/50 text-xs text-secondary italic leading-relaxed break-words">
                      {r.message}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => grantAccess(r.user_id, r.id, r.guild_id)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50"
                >
                  {r.guild_id === null ? "Grant view" : "Grant"}
                </button>
                <button
                  onClick={() => denyRequest(r.id)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-50"
                >
                  Deny
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center gap-3 flex-wrap">
          <h2 className="text-sm font-medium shrink-0">
            Accounts <span className="text-xs text-muted">{users === null ? "…" : filteredUsers.length}</span>
          </h2>
          <input
            type="search"
            value={search}
            placeholder="Search"
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs text-primary placeholder:text-muted focus:outline-none focus:border-accent/60 min-w-0 flex-1"
          />
        </div>
        <div className="divide-y divide-border">
          {users === null && <p className="px-4 py-3 text-xs text-muted">Loading accounts...</p>}
          {users !== null && users.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted">No accounts yet.</p>
          )}
          {users !== null && users.length > 0 && filteredUsers.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted">No account matches "{search}".</p>
          )}
          {pageRows.map((u) => {
            const detail = details[u.id];
            const detailData = detail && detail !== "loading" ? detail : null;
            const anyDirty = modalDirty(u);
            return (
              <div key={u.id} className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => openEditor(u)}
                    className="flex items-center gap-3 flex-1 min-w-0 text-left"
                    aria-haspopup="dialog"
                  >
                    <Avatar src={u.avatar_url} name={u.username} />
                    <span className="min-w-0">
                      <span className="text-sm text-primary flex items-center gap-2">
                        {u.username}
                        {u.id === myId && <span className="text-xs text-muted">(you)</span>}
                        {u.seed && <SeedBadge />}
                        {anyDirty && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                            unsaved
                          </span>
                        )}
                      </span>
                      <span
                        className="block text-xs text-muted truncate"
                        title={subtitleFor(u).full}
                      >
                        {subtitleFor(u).text}
                      </span>
                    </span>
                  </button>
                  {!u.seed && (
                    <button
                      onClick={() => setConfirmDelete(u.id)}
                      aria-label={`Remove ${u.username}`}
                      title="Remove account"
                      disabled={busy !== null}
                      className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-red-400 hover:bg-red-500/10 transition disabled:opacity-40"
                    >
                      <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                        <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6h12z" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {filteredUsers.length > pageSize && (
          <div className="px-4 py-2.5 border-t border-border flex items-center justify-between gap-3 flex-wrap">
            <span className="text-xs text-muted">
              Page {safePage} of {totalPages} ({filteredUsers.length} account{filteredUsers.length === 1 ? "" : "s"})
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(safePage - 1)}
                disabled={safePage <= 1 || busy !== null}
                aria-label="Previous page"
                className="w-7 h-7 flex items-center justify-center rounded-lg bg-card border border-border text-secondary hover:text-primary transition disabled:opacity-40"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M15 18l-6-6 6-6" />
                </svg>
              </button>
              <button
                onClick={() => setPage(safePage + 1)}
                disabled={safePage >= totalPages || busy !== null}
                aria-label="Next page"
                className="w-7 h-7 flex items-center justify-center rounded-lg bg-card border border-border text-secondary hover:text-primary transition disabled:opacity-40"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <path d="M9 18l6-6-6-6" />
                </svg>
              </button>
              <select
                value={pageSize}
                disabled={busy !== null}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                  localStorage.setItem("pakodj:users-page-size", e.target.value);
                }}
                aria-label="Accounts per page"
                className="px-2 py-1 rounded-lg bg-card border border-border text-xs text-secondary"
              >
                {[8, 16, 32, 64].map((n) => (
                  <option key={n} value={n}>{n} / page</option>
                ))}
              </select>
            </div>
          </div>
        )}
      </section>

      {/* Account editor dialog: staging, per-guild overrides and the removal
          flow live inside it; Escape/backdrop/X all close and wipe staging. */}
      {openId !== null && (() => {
        const u = (users ?? []).find((x) => x.id === openId);
        if (!u) return null;
        const detail = details[u.id];
        const detailData = detail && detail !== "loading" ? detail : null;
        // Servers already covered: saved overrides plus rows staged now.
        const overridden = new Set([
          ...(detailData?.guild_overrides ?? []).map((o) => o.guild_id),
          ...(pendingGuild[u.id] ?? []),
        ]);
        // Whole-dialog dirtiness; the footer bar is the single commit point.
        const dirtyModal = modalDirty(u);
        return (
          <>
            <div
              className="fixed inset-0 z-50 bg-black/60 flex items-start sm:items-center justify-center p-4 overflow-y-auto"
              onClick={() => closeEditor(u.id)}
            >
              <div
                className="bg-card border border-border rounded-xl w-full max-w-2xl shadow-xl flex flex-col max-h-[85vh]"
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="px-5 py-4 flex items-center gap-3 border-b border-border">
                  <Avatar src={u.avatar_url} name={u.username} size="w-10 h-10" />
                  <div className="min-w-0">
                    <p className="text-sm text-primary flex items-center gap-2">
                      {u.username}
                      {u.id === myId && <span className="text-xs text-muted">(you)</span>}
                      {u.seed && <SeedBadge />}
                      {dirtyModal && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                          unsaved
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-muted truncate">{subtitleFor(u).text}</p>
                  </div>
                  <button
                    onClick={() => closeEditor(u.id)}
                    aria-label="Close"
                    className="ml-auto shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-muted hover:text-primary hover:bg-card transition"
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>

                <div className="px-5 py-4 overflow-y-auto">
                  {u.seed ? (
                    <p className="text-xs text-muted">
                      Seeded account owned by DISCORD_ADMIN_IDS: it always holds every
                      permission and cannot be edited.
                    </p>
                  ) : (
                    <>
                      <p className="text-[11px] uppercase tracking-wide text-muted mb-2">
                        Global permissions
                      </p>
                      <div className="flex flex-wrap gap-1.5 items-center mb-2">
                        {PERMISSIONS.map((perm) => {
                          const needsView = perm !== "view" && !permsFor(u).includes("view");
                          return (
                            <Chip
                              key={perm}
                              active={permsFor(u).includes(perm)}
                              disabled={busy !== null || needsView}
                              label={perm}
                              hint={needsView ? "Enable view first: nothing can exist without it" : undefined}
                              onClick={() =>
                                setStagedGlobal((s) => ({
                                  ...s,
                                  [u.id]: stagePerm(permsFor(u), perm, !permsFor(u).includes(perm)),
                                }))
                              }
                            />
                          );
                        })}
                      </div>

                      <p className="text-[11px] uppercase tracking-wide text-muted mb-2 mt-4">
                        Per-guild overrides
                      </p>
                      {detail === undefined || detail === "loading" ? (
                        <p className="text-xs text-muted">Loading overrides...</p>
                      ) : (
                        <div className="space-y-2">
                          {detailData?.guild_overrides.length === 0 && (pendingGuild[u.id] ?? []).length === 0 && (
                            sharedGuildIds(u).length === 0 && permsFor(u).length === 0 ? (
                              <p className="text-xs text-muted">
                                This account shares no server with the bot yet.
                              </p>
                            ) : permsFor(u).length === 0 ? (
                              <p className="text-xs text-muted">
                                No override: without one the account sees nothing.
                              </p>
                            ) : (
                              <p className="text-xs text-muted">
                                No override: the global set applies everywhere.
                              </p>
                            )
                          )}
                          {detailData &&
                            detailData.guild_overrides.map((o) => {
                              const row = rowView(u, o.guild_id, o);
                              return (
                                <div
                                  key={o.guild_id}
                                  className={`flex items-center gap-3 flex-wrap bg-surface border rounded-lg p-2 ${
                                    row.removed ? "opacity-60 border-red-500/40" : "border-border"
                                  }`}
                                >
                                  <span className="text-xs text-secondary w-40 truncate" title={o.guild_id}>
                                    {guilds.find((g) => g.id === o.guild_id)?.name ?? o.guild_id}
                                  </span>
                                  {!row.removed && (
                                    <div className="flex flex-wrap gap-1.5 flex-1 min-w-40">
                                      {GUILD_PERMISSIONS.map((perm) => {
                                        const needsView = perm !== "view" && !row.permissions.includes("view");
                                        return (
                                          <Chip
                                            key={perm}
                                            active={row.permissions.includes(perm)}
                                            disabled={busy !== null || needsView}
                                            label={perm}
                                            hint={needsView ? "Enable view first: nothing can exist without it" : undefined}
                                            onClick={() =>
                                              setStagedGuild((s) => ({
                                                ...s,
                                                [`${u.id}:${o.guild_id}`]: {
                                                  permissions: stagePerm(row.permissions, perm, !row.permissions.includes(perm)),
                                                  removed: false,
                                                },
                                              }))
                                            }
                                          />
                                        );
                                      })}
                                    </div>
                                  )}
                                  <span className="ml-auto">
                                    <button
                                      onClick={() =>
                                        setStagedGuild((s) => ({
                                          ...s,
                                          [`${u.id}:${o.guild_id}`]: {
                                            permissions: row.permissions,
                                            removed: !row.removed,
                                          },
                                        }))
                                      }
                                      disabled={busy !== null}
                                      className="px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition disabled:opacity-40"
                                    >
                                      {row.removed ? "Keep" : "Remove"}
                                    </button>
                                  </span>
                                </div>
                              );
                            })}
                          {detailData && (
                            <>
                              {/* Pending (staged but unsaved) override rows.
                                  The add control below stays visible with them. */}
                              {(pendingGuild[u.id] ?? []).map((gid) => {
                                const prow = rowView(u, gid);
                                return (
                                  <div
                                    key={gid}
                                    className="flex items-center gap-3 flex-wrap bg-surface border border-accent/50 border-dashed rounded-lg p-2"
                                  >
                                    <span className="text-xs text-secondary w-40 truncate">
                                      {guilds.find((g) => g.id === gid)?.name ?? gid}
                                    </span>
                                    <div className="flex flex-wrap gap-1.5 flex-1 min-w-40">
                                      {GUILD_PERMISSIONS.map((perm) => {
                                        const needsView = perm !== "view" && !prow.permissions.includes("view");
                                        return (
                                          <Chip
                                            key={perm}
                                            active={prow.permissions.includes(perm)}
                                            disabled={busy !== null || needsView}
                                            label={perm}
                                            hint={needsView ? "Enable view first: nothing can exist without it" : undefined}
                                            onClick={() =>
                                              setStagedGuild((s) => ({
                                                ...s,
                                                [`${u.id}:${gid}`]: {
                                                  permissions: stagePerm(prow.permissions, perm, !prow.permissions.includes(perm)),
                                                  removed: false,
                                                },
                                              }))
                                            }
                                          />
                                        );
                                      })}
                                    </div>
                                    <span className="ml-auto">
                                      <button
                                        onClick={() => {
                                          setPendingGuild((s) => ({
                                            ...s,
                                            [u.id]: (s[u.id] ?? []).filter((x) => x !== gid),
                                          }));
                                          setStagedGuild((s) => { const c = { ...s }; delete c[`${u.id}:${gid}`]; return c; });
                                        }}
                                        disabled={busy !== null}
                                        className="px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition disabled:opacity-40"
                                      >
                                        Remove
                                      </button>
                                    </span>
                                  </div>
                                );
                              })}
                              {/* Add control: one option per bot server not
                                  yet overridden or staged here. Hidden when
                                  nothing is left to add. */}
                              {guilds.some((g) => !overridden.has(g.id)) && (
                                <select
                                  value=""
                                  disabled={busy !== null}
                                  onChange={(e) => {
                                    const v = e.target.value;
                                    if (v) stagePending(u, [v]);
                                  }}
                                  className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary"
                                >
                                  <option value="">Add a server override...</option>
                                  {guilds
                                    .filter((g) => !overridden.has(g.id))
                                    .map((g) => (
                                      <option key={g.id} value={g.id}>{g.name}</option>
                                    ))}
                                </select>
                              )}
                            </>
                          )}
                        </div>
                      )}

                    </>
                  )}
                </div>

                {/* Single bottom commit bar for the whole dialog. */}
                <div
                  className="px-5 py-3 border-t border-border flex items-center gap-2 shrink-0"
                  style={{ backgroundColor: "var(--color-card, inherit)" }}
                >
                  <span className="text-xs text-muted flex-1 min-w-40">
                    {dirtyModal ? "Unsaved changes: they go in one batch on Save." : "No pending change."}
                  </span>
                  <button
                    onClick={() => clearStaging(u.id)}
                    disabled={!dirtyModal || busy !== null}
                    className="px-3 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-40"
                  >
                    Reset
                  </button>
                  <button
                    onClick={() => saveAll(u)}
                    disabled={!dirtyModal || busy !== null}
                    className="px-3 py-1.5 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-40"
                  >
                    Save
                  </button>
                </div>
              </div>
            </div>

          </>
        );
      })()}

      {/* Second step of the removal: a page-level confirm dialog. It lives
          OUTSIDE the modal so the trash button works from the plain list,
          and stacks above the modal when the editor is open. */}
      {(() => {
        const target = users?.find((x) => x.id === confirmDelete);
        if (!target) return null;
        return (
          <div
            className="fixed inset-0 z-[70] bg-black/70 flex items-center justify-center p-4"
            onClick={() => setConfirmDelete(null)}
          >
            <div
              className="bg-card border border-red-500/40 rounded-xl max-w-sm w-full p-5 shadow-xl"
              onClick={(e) => e.stopPropagation()}
              role="alertdialog"
              aria-modal="true"
            >
              <h3 className="text-sm font-medium text-primary">Remove {target.username}?</h3>
              <p className="text-xs text-muted mt-2">
                This deletes the account together with its sessions and access
                requests. The user can log in again later, starting from zero.
              </p>
              <div className="flex justify-end gap-2 mt-4">
                <button
                  onClick={() => setConfirmDelete(null)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={() => deleteUser(target.id)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-red-500/90 text-inverse text-xs font-medium hover:bg-red-500 transition disabled:opacity-50"
                >
                  Confirm remove
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </main>
  );
}