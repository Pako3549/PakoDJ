import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "@/lib/api";
import { useAccount, PERMISSIONS } from "@/lib/auth";
import { useEscapeBack } from "@/lib/useEscapeBack";
import { readCache, writeCache } from "@/lib/cache";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type User = {
  id: number;
  username: string;
  permissions: string[];
  seed: boolean;
  discord_id: string;
  avatar_url: string | null;
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

// Toggling helper: add the permission when missing, remove when present.
function withPermission(list: string[], perm: string, on: boolean): string[] {
  return on ? [...new Set([...list, perm])] : list.filter((p) => p !== perm);
}

// Short human descriptions next to the toggles.
const PERMISSION_DESC: Record<string, string> = {
  view: "See servers, queues and now playing",
  search: "Search tracks and listen to previews",
  queue: "Play and add tracks to the queue",
  queue_manage: "Remove, reorder and clear the queue",
  playback: "Pause, resume, skip and stop",
  manage_users: "Manage accounts, permissions and requests",
};

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
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={PERMISSION_DESC[label]}
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

function SaveCancel({
  onSave,
  onReset,
  busy,
  dirty,
  saveLabel = "Save",
}: {
  onSave: () => void;
  onReset: () => void;
  busy: boolean;
  dirty: boolean;
  saveLabel?: string;
}) {
  if (!dirty) return null;
  return (
    <span className="flex items-center gap-2">
      <button
        onClick={onSave}
        disabled={busy}
        className="px-3 py-1 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50"
      >
        {saveLabel}
      </button>
      <button
        onClick={onReset}
        disabled={busy}
        className="px-2 py-1 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-50"
      >
        Reset
      </button>
    </span>
  );
}

export default function UsersPage() {
  const { account, loading, error: authError } = useAccount("manage_users");
  useEscapeBack();
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
  const [pendingGuild, setPendingGuild] = useState<Record<number, string | null>>({});
  const [pendingStaged, setPendingStaged] = useState<Record<number, string[]>>({});
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

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
    const next = openId === u.id ? null : u.id;
    setOpenId(next);
    // Closing an editor wipes its unapplied staging: explicit, expected.
    if (next === null) {
      setStagedGlobal((s) => { const c = { ...s }; delete c[u.id]; return c; });
      setPendingGuild((s) => { const c = { ...s }; delete c[u.id]; return c; });
      setPendingStaged((s) => { const c = { ...s }; delete c[u.id]; return c; });
      setConfirmDelete(null);
      setStagedGuild((s) =>
        Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith(`${u.id}:`)))
      );
    }
    if (next !== null && !details[next]) {
      setDetails((d) => ({ ...d, [next]: "loading" }));
      apiFetch<UserDetail>(`/api/users/${next}`)
        .then((detail) => setDetails((d) => ({ ...d, [next]: detail })))
        .catch(() => setDetails((d) => ({ ...d, [next]: "loading" })));
    }
  };

  const permsFor = (u: User) => stagedGlobal[u.id] ?? u.permissions;
  const globalDirty = (u: User) => u.id in stagedGlobal && !same(stagedGlobal[u.id], u.permissions);

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

  const saveGlobal = (u: User) => {
    const staged = stagedGlobal[u.id];
    if (!staged) return;
    run(`global:${u.id}`, () =>
      apiFetch(`/api/users/${u.id}`, {
        method: "PATCH",
        body: JSON.stringify({ permissions: staged }),
      })
    ).then(() => setStagedGlobal((s) => { const c = { ...s }; delete c[u.id]; return c; }));
  };

  const overrideFor = (u: User, o: GuildOverride): RowStaged => {
    const st = stagedGuild[`${u.id}:${o.guild_id}`];
    return { permissions: st?.permissions ?? o.permissions, removed: st?.removed ?? false };
  };
  const overrideDirty = (u: User, o: GuildOverride) => {
    const st = stagedGuild[`${u.id}:${o.guild_id}`];
    if (!st) return false;
    if (st.removed) return true;
    return !same(st.permissions, o.permissions);
  };

  const saveOverride = (u: User, o: GuildOverride) => {
    const st = stagedGuild[`${u.id}:${o.guild_id}`];
    if (!st) return;
    const req = st.removed
      ? apiFetch(`/api/users/${u.id}/guilds/${o.guild_id}`, { method: "DELETE" })
      : apiFetch(`/api/users/${u.id}/guilds/${o.guild_id}`, {
          method: "PUT",
          body: JSON.stringify({ permissions: st.permissions }),
        });
    run(`guild:${u.id}:${o.guild_id}`, () => req).then(() =>
      setStagedGuild((s) => { const c = { ...s }; delete c[`${u.id}:${o.guild_id}`]; return c; })
    );
  };

  const pendingRow = (u: User): { guildId: string; permissions: string[] } | null => {
    const gid = pendingGuild[u.id];
    return gid ? { guildId: gid, permissions: pendingStaged[u.id] ?? ["view"] } : null;
  };

  const savePending = (u: User) => {
    const row = pendingRow(u);
    if (!row) return;
    run(`guild:${u.id}:${row.guildId}`, () =>
      apiFetch(`/api/users/${u.id}/guilds/${row.guildId}`, {
        method: "PUT",
        body: JSON.stringify({ permissions: row.permissions }),
      })
    ).then(() => {
      setPendingGuild((s) => { const c = { ...s }; delete c[u.id]; return c; });
      setPendingStaged((s) => { const c = { ...s }; delete c[u.id]; return c; });
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

  return (
    <main className="max-w-3xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Link to="/" className="px-3 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition">
            ←
          </Link>
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
                      <>wants <span className="text-muted">generic access (no shared server)</span></>
                    ) : (
                      <>
                        wants <span className="text-muted">{r.guild_name || r.guild_id}</span>
                      </>
                    )}
                    {r.message && <span> · {r.message}</span>}
                  </p>
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
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          <h2 className="text-sm font-medium">Accounts</h2>
          <span className="text-xs text-muted">{users === null ? "…" : users.length}</span>
        </div>
        <div className="divide-y divide-border">
          {users === null && <p className="px-4 py-3 text-xs text-muted">Loading accounts...</p>}
          {users !== null && users.length === 0 && (
            <p className="px-4 py-3 text-xs text-muted">No accounts yet.</p>
          )}
          {(users ?? []).map((u) => {
            const isOpen = openId === u.id;
            const detail = details[u.id];
            const detailData = detail && detail !== "loading" ? detail : null;
            const overridden = new Set((detailData?.guild_overrides ?? []).map((o) => o.guild_id));
            const anyDirty =
              globalDirty(u) ||
              (detailData?.guild_overrides ?? []).some((o) => overrideDirty(u, o)) ||
              pendingGuild[u.id] !== undefined;
            return (
              <div key={u.id} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => openEditor(u)}
                    className="flex items-center gap-3 flex-1 min-w-0 text-left"
                    aria-expanded={isOpen}
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
                      <span className="block text-xs text-muted truncate">
                        {permsFor(u).length ? permsFor(u).join(" · ") : "no permissions yet"}
                      </span>
                    </span>
                  </button>
                  <svg
                    viewBox="0 0 24 24"
                    className={`w-4 h-4 text-muted transition-transform ${isOpen ? "rotate-180" : ""}`}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </div>

                {isOpen && (
                  <div className="mt-4">
                    {u.seed ? (
                      <p className="text-xs text-muted mb-3">
                        Seeded account owned by DISCORD_ADMIN_IDS: it always holds every
                        permission and cannot be edited.
                      </p>
                    ) : (
                      <>
                        <p className="text-[11px] uppercase tracking-wide text-muted mb-2">
                          Global permissions
                        </p>
                        <div className="flex flex-wrap gap-1.5 items-center mb-2">
                          {PERMISSIONS.map((perm) => (
                            <Chip
                              key={perm}
                              active={permsFor(u).includes(perm)}
                              disabled={busy !== null}
                              label={perm}
                              onClick={() =>
                                setStagedGlobal((s) => ({
                                  ...s,
                                  [u.id]: withPermission(permsFor(u), perm, !permsFor(u).includes(perm)),
                                }))
                              }
                            />
                          ))}
                          <SaveCancel
                            busy={busy !== null}
                            dirty={globalDirty(u)}
                            onSave={() => saveGlobal(u)}
                            onReset={() => setStagedGlobal((s) => { const c = { ...s }; delete c[u.id]; return c; })}
                          />
                        </div>

                        <p className="text-[11px] uppercase tracking-wide text-muted mb-2">
                          Per-guild overrides
                        </p>
                        {detail === undefined || detail === "loading" ? (
                          <p className="text-xs text-muted mb-4">Loading overrides...</p>
                        ) : (
                          <div className="space-y-2 mb-3">
                            {detailData === null ||
                            (detailData.guild_overrides.length === 0 && pendingRow(u) === null) ? (
                              sharedGuildIds(u).length === 0 ? (
                                <p className="text-xs text-muted">
                                  This account shares no server with the bot yet.
                                </p>
                              ) : (
                                <p className="text-xs text-muted">
                                  No override: the global set applies everywhere.
                                </p>
                              )
                            ) : (
                              <>
                                {detailData &&
                                  detailData.guild_overrides.map((o) => {
                                    const row = overrideFor(u, o);
                                    const dirty = overrideDirty(u, o);
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
                                          <div className="flex flex-wrap gap-1.5 flex-1">
                                            {PERMISSIONS.map((perm) => (
                                              <Chip
                                                key={perm}
                                                active={row.permissions.includes(perm)}
                                                disabled={busy !== null}
                                                label={perm}
                                                onClick={() =>
                                                  setStagedGuild((s) => ({
                                                    ...s,
                                                    [`${u.id}:${o.guild_id}`]: {
                                                      permissions: withPermission(row.permissions, perm, !row.permissions.includes(perm)),
                                                      removed: false,
                                                    },
                                                  }))
                                                }
                                              />
                                            ))}
                                          </div>
                                        )}
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
                                        <SaveCancel
                                          busy={busy !== null}
                                          dirty={dirty}
                                          saveLabel={row.removed ? "Delete" : "Save"}
                                          onSave={() => saveOverride(u, o)}
                                          onReset={() =>
                                            setStagedGuild((s) => { const c = { ...s }; delete c[`${u.id}:${o.guild_id}`]; return c; })
                                          }
                                        />
                                      </div>
                                    );
                                  })}
                                {pendingRow(u) === null ? (
                                  <select
                                    value=""
                                    disabled={busy !== null}
                                    onChange={(e) => e.target.value && setPendingGuild((s) => ({ ...s, [u.id]: e.target.value }))}
                                    className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary"
                                  >
                                    <option value="">Override a specific server...</option>
                                    {guilds
                                      .filter((g) => !overridden.has(g.id))
                                      .map((g) => (
                                        <option key={g.id} value={g.id}>{g.name}</option>
                                      ))}
                                  </select>
                                ) : (
                                  pendingRow(u) && (
                                    <div className="flex items-center gap-3 flex-wrap bg-surface border border-accent/50 border-dashed rounded-lg p-2">
                                      <span className="text-xs text-secondary w-40 truncate">
                                        {guilds.find((g) => g.id === pendingRow(u)!.guildId)?.name ?? pendingRow(u)!.guildId}
                                      </span>
                                      <div className="flex flex-wrap gap-1.5 flex-1">
                                        {PERMISSIONS.map((perm) => (
                                          <Chip
                                            key={perm}
                                            active={pendingRow(u)!.permissions.includes(perm)}
                                            disabled={busy !== null}
                                            label={perm}
                                            onClick={() =>
                                              setPendingStaged((s) => ({
                                                ...s,
                                                [u.id]: withPermission(pendingRow(u)!.permissions, perm, !pendingRow(u)!.permissions.includes(perm)),
                                              }))
                                            }
                                          />
                                        ))}
                                      </div>
                                      <SaveCancel
                                        busy={busy !== null}
                                        dirty
                                        onSave={() => savePending(u)}
                                        onReset={() => {
                                          setPendingGuild((s) => { const c = { ...s }; delete c[u.id]; return c; });
                                          setPendingStaged((s) => { const c = { ...s }; delete c[u.id]; return c; });
                                        }}
                                      />
                                    </div>
                                  )
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </>
                    )}

                    {!u.seed && (
                      <div className="mt-4 pt-3 border-t border-border flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-muted flex-1 min-w-40">
                          Removing the account deletes its sessions and access
                          requests; the user can log in again at any time.
                        </span>
                        {confirmDelete === u.id ? (
                          <>
                            <button
                              onClick={() => deleteUser(u.id)}
                              disabled={busy !== null}
                              className="px-3 py-1 rounded-lg bg-red-500/90 text-inverse text-xs font-medium hover:bg-red-500 transition disabled:opacity-50"
                            >
                              Confirm remove
                            </button>
                            <button
                              onClick={() => setConfirmDelete(null)}
                              disabled={busy !== null}
                              className="px-2 py-1 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-50"
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setConfirmDelete(u.id)}
                            disabled={busy !== null}
                            className="px-2 py-1 rounded-lg bg-card border border-border text-xs text-red-400 hover:text-red-300 transition disabled:opacity-40"
                          >
                            Remove account
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </main>
  );
}
