import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, ApiError } from "@/lib/api";
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

type UserDetail = User & { guild_overrides: GuildOverride[] };

type AccessRequest = {
  id: number;
  user_id: number;
  username: string;
  guild_id: number;
  guild_name: string;
  message: string;
  created_at: string;
};

type Guild = { id: string; name: string };

// Short human descriptions next to the toggles.
const PERMISSION_DESC: Record<string, string> = {
  view: "See servers, queues and now playing",
  search: "Search tracks and listen to previews",
  queue: "Play and add tracks to the queue",
  queue_manage: "Remove, reorder and clear the queue",
  playback: "Pause, resume, skip and stop",
  manage_users: "Manage accounts, permissions and requests",
};

function withPermission(list: string[], perm: string, on: boolean): string[] {
  return on ? [...new Set([...list, perm])] : list.filter((p) => p !== perm);
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
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`px-2.5 py-1 text-xs rounded-full border transition ${
        active
          ? "bg-accent/20 border-accent/40 text-accent font-medium"
          : "border-border text-muted hover:text-secondary hover:border-accent/30"
      } disabled:opacity-40`}
    >
      {children}
    </button>
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

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError("");
    try {
      await fn();
      await reload();
      // Keep the open editor live with fresh override data.
      const editorId = openId;
      if (editorId !== null) {
        const fresh = await apiFetch<UserDetail>(`/api/users/${editorId}`);
        setDetails((d) => ({ ...d, [editorId]: fresh }));
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const openEditor = (u: User) => {
    const next = openId === u.id ? null : u.id;
    setOpenId(next);
    if (next !== null && !details[next]) {
      setDetails((d) => ({ ...d, [next]: "loading" }));
      apiFetch<UserDetail>(`/api/users/${next}`)
        .then((detail) => setDetails((d) => ({ ...d, [next]: detail })))
        .catch(() => setDetails((d) => ({ ...d, [next]: "loading" })));
    }
  };

  const toggleGlobal = (userId: number, perm: string, next: boolean) => {
    const user = users?.find((u) => u.id === userId);
    if (!user) return;
    setUsers((list) =>
      (list ?? []).map((u) => (u.id === userId ? { ...u, permissions: withPermission(u.permissions, perm, next) } : u))
    );
    run(`global:${userId}`, () =>
      apiFetch(`/api/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ permissions: withPermission(user.permissions, perm, next) }),
      })
    );
  };

  const toggleGuild = (userId: number, guildId: string, permissions: string[], perm: string, next: boolean) =>
    run(`guild:${userId}:${guildId}`, () =>
      apiFetch(`/api/users/${userId}/guilds/${guildId}`, {
        method: "PUT",
        body: JSON.stringify({ permissions: withPermission(permissions, perm, next) }),
      })
    );

  const addOverride = (userId: number, guildId: string) =>
    run(`guild:${userId}:${guildId}`, () =>
      apiFetch(`/api/users/${userId}/guilds/${guildId}`, {
        method: "PUT",
        body: JSON.stringify({ permissions: ["view"] }),
      })
    );

  const removeOverride = (userId: number, guildId: string) =>
    run(`guild:${userId}:${guildId}`, () =>
      apiFetch(`/api/users/${userId}/guilds/${guildId}`, { method: "DELETE" })
    );

  const grantAccess = (userId: number, requestId: number) =>
    run(`global:${userId}`, () =>
      apiFetch(`/api/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ permissions: ["view"] }),
      }).then(() =>
        apiFetch(`/api/access-requests/${requestId}`, {
          method: "PATCH",
          body: JSON.stringify({ status: "granted" }),
        })
      )
    );

  const denyRequest = (requestId: number) =>
    run(`deny:${requestId}`, () =>
      apiFetch(`/api/access-requests/${requestId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "denied" }),
      })
    );

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
                    wants <span className="text-muted">{r.guild_name || r.guild_id}</span>
                    {r.message && <span> · {r.message}</span>}
                  </p>
                </div>
                <button
                  onClick={() => grantAccess(r.user_id, r.id)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50"
                >
                  Grant view
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
                      </span>
                      <span className="block text-xs text-muted truncate">
                        {u.permissions.length
                          ? u.permissions.join(" · ")
                          : "no permissions yet"}
                        {detailData && detailData.guild_overrides.length > 0 && (
                          <span> · {detailData.guild_overrides.length} guild override(s)</span>
                        )}
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
                        <div className="flex flex-wrap gap-1.5 mb-1">
                          {PERMISSIONS.map((perm) => (
                            <Chip
                              key={perm}
                              active={u.permissions.includes(perm)}
                              disabled={busy !== null}
                              title={PERMISSION_DESC[perm]}
                              onClick={() => toggleGlobal(u.id, perm, !u.permissions.includes(perm))}
                            >
                              {perm}
                            </Chip>
                          ))}
                        </div>
                        <p className="text-[11px] text-muted mb-4">
                          {PERMISSION_DESC[u.permissions[0]] ?? "Empty set: the account reads nothing but can still ask for access."}
                        </p>

                        <p className="text-[11px] uppercase tracking-wide text-muted mb-2">
                          Per-guild overrides (replace the global set inside that guild)
                        </p>
                        {detail === undefined || detail === "loading" ? (
                          <p className="text-xs text-muted mb-4">Loading overrides...</p>
                        ) : (
                          <div className="space-y-2 mb-3">
                            {detailData === null || detailData.guild_overrides.length === 0 ? (
                              <p className="text-xs text-muted">No override: the global set applies everywhere.</p>
                            ) : (
                              detailData &&
                              detailData.guild_overrides.map((o) => (
                                <div
                                  key={o.guild_id}
                                  className="flex items-center gap-3 flex-wrap bg-surface border border-border rounded-lg p-2"
                                >
                                  <span className="text-xs text-secondary w-40 truncate" title={o.guild_id}>
                                    {guilds.find((g) => g.id === o.guild_id)?.name ?? o.guild_id}
                                  </span>
                                  <div className="flex flex-wrap gap-1.5 flex-1">
                                    {PERMISSIONS.map((perm) => (
                                      <Chip
                                        key={perm}
                                        active={o.permissions.includes(perm)}
                                        disabled={busy !== null}
                                        title={PERMISSION_DESC[perm]}
                                        onClick={() =>
                                          toggleGuild(u.id, o.guild_id, o.permissions, perm, !o.permissions.includes(perm))
                                        }
                                      >
                                        {perm}
                                      </Chip>
                                    ))}
                                  </div>
                                  <button
                                    onClick={() => removeOverride(u.id, o.guild_id)}
                                    disabled={busy !== null}
                                    className="px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition disabled:opacity-40"
                                  >
                                    Remove
                                  </button>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                        <select
                          value=""
                          disabled={busy !== null}
                          onChange={(e) => e.target.value && addOverride(u.id, e.target.value)}
                          className="px-3 py-2 rounded-lg bg-card border border-border text-xs text-secondary"
                        >
                          <option value="">Override a specific server...</option>
                          {guilds
                            .filter((g) => !overridden.has(g.id))
                            .map((g) => (
                              <option key={g.id} value={g.id}>{g.name}</option>
                            ))}
                        </select>
                      </>
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
