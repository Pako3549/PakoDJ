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

type GuildOverride = { guild_id: string; guild_name?: string; permissions: string[] };

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

// Toggling helper: add the permission when missing, remove when present.
function withPermission(list: string[], perm: string, on: boolean): string[] {
  return on ? [...new Set([...list, perm])] : list.filter((p) => p !== perm);
}

function Avatar({ src, name, size = "w-9 h-9" }: { src: string | null; name: string; size?: string }) {
  // Discord avatar when known, otherwise a first-letter placeholder.
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
  // Text + lock glyph only: no emoji, per docs/style.md.
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

function PermissionPills({
  permissions,
  onToggle,
  disabled,
}: {
  permissions: string[];
  onToggle?: (perm: string, next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1" role="group">
      {PERMISSIONS.map((perm) => {
        const active = permissions.includes(perm);
        return (
          <button
            key={perm}
            onClick={() => onToggle?.(perm, !active)}
            disabled={disabled}
            title={active ? `Grant: on (${perm})` : `Grant: off (${perm})`}
            className={`px-2.5 py-1 text-xs rounded-full border transition ${
              active
                ? "bg-accent/20 border-accent/40 text-accent font-medium"
                : "border-border text-muted hover:text-secondary hover:border-accent/30"
            } disabled:opacity-40`}
          >
            {perm}
          </button>
        );
      })}
    </div>
  );
}

export default function UsersPage() {
  const { account, loading, error: authError } = useAccount("manage_users");
  // The header has a back arrow: Escape goes back to the dashboard.
  useEscapeBack();
  // Hydrate from the last fetched values: counts and rows render instantly
  // (or as "…" placeholders when no cache) instead of fake zero defaults.
  const [users, setUsers] = useState<User[] | null>(() => readCache<User[]>("pakodj:users", 60_000));
  const [requests, setRequests] = useState<AccessRequest[] | null>(() =>
    readCache<AccessRequest[]>("pakodj:access-requests", 30_000)
  );
  const [guilds, setGuilds] = useState<Guild[]>([]);
  const [expanded, setExpanded] = useState<UserDetail | null>(null);
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
      // Keep the open editor in sync with fresh data.
      if (expanded) {
        setExpanded(await apiFetch<UserDetail>(`/api/users/${expanded.id}`).catch(() => null));
      }
    } catch (err) {
      setError((err as Error).message);
    }
  }, [expanded]);

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
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const toggleGlobal = (userId: number, perm: string, next: boolean) => {
    const user = users?.find((u) => u.id === userId);
    if (!user) return;
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
      }).then(() => apiFetch(`/api/access-requests/${requestId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "granted" }),
      }))
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
  const overriddenGuildIds = new Set((expanded?.guild_overrides ?? []).map((o) => o.guild_id));

  return (
    <main className="max-w-5xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
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

      {error && <p className="text-xs text-red-400 mb-4">{error}</p>}

      {(requests !== null && requests.length > 0) && (
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
                    wants access to <span className="text-muted">{r.guild_name || r.guild_id}</span>
                    {r.message && <span> · “{r.message}”</span>}
                  </p>
                </div>
                <button
                  onClick={() => grantAccess(r.user_id, r.id)}
                  disabled={busy !== null}
                  className="px-3 py-1.5 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50"
                >
                  Grant
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
        <div className="p-4 space-y-2">
          {users === null && <p className="text-xs text-muted">Loading accounts...</p>}
          {users !== null && users.length === 0 && (
            <p className="text-xs text-muted">No accounts yet (first Discord login creates them).</p>
          )}
          {(users ?? []).map((u) => {
            const isOpen = expanded?.id === u.id;
            return (
              <div key={u.id} className="bg-surface border border-border rounded-lg p-3">
                <div className="flex items-center gap-3">
                  <Avatar src={u.avatar_url} name={u.username} />
                  <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpanded(isOpen ? null : { ...u, guild_overrides: [] })}>
                    <p className="text-sm text-primary truncate flex items-center gap-2">
                      {u.username}
                      {u.id === myId && <span className="text-xs text-muted">(you)</span>}
                      {u.seed && <SeedBadge />}
                    </p>
                    <p className="text-xs text-muted font-mono truncate">{u.discord_id}</p>
                  </div>
                  <PermissionPills permissions={u.permissions} />
                  <button
                    onClick={() => setExpanded(isOpen ? null : { ...u, guild_overrides: [] })}
                    className="px-2.5 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary hover:text-primary transition"
                    aria-expanded={isOpen}
                  >
                    {isOpen ? "Close guild overrides" : "Guild overrides"}
                  </button>
                </div>

                {isOpen && (
                  <div className="mt-3 pt-3 border-t border-border space-y-2">
                    {expanded?.guild_overrides.map((o) => (
                      <div key={o.guild_id} className="flex items-center gap-3 flex-wrap bg-card border border-border rounded-lg p-2">
                        <span className="text-xs text-secondary w-40 truncate" title={o.guild_id}>
                          {guilds.find((g) => g.id === o.guild_id)?.name ?? o.guild_id}
                        </span>
                        <PermissionPills
                          permissions={o.permissions}
                          disabled={busy !== null || u.seed}
                          onToggle={(perm, next) => u.id && toggleGuild(u.id, o.guild_id, o.permissions, perm, next)}
                        />
                        <button
                          onClick={() => removeOverride(u.id, o.guild_id)}
                          disabled={busy !== null || u.seed}
                          className="ml-auto px-2 py-1 rounded bg-card border border-border text-xs text-red-400 hover:text-red-300 transition disabled:opacity-40"
                        >
                          Remove override
                        </button>
                      </div>
                    ))}
                    {expanded && expanded.guild_overrides.length === 0 && (
                      <p className="text-xs text-muted">No per-guild override: the global set applies everywhere.</p>
                    )}
                    <div className="flex items-center gap-2">
                      <select
                        value=""
                        disabled={busy !== null || u.seed}
                        onChange={(e) => e.target.value && addOverride(u.id, e.target.value)}
                        className="px-2 py-1.5 rounded-lg bg-card border border-border text-xs text-secondary"
                      >
                        <option value="">Add override for guild...</option>
                        {guilds
                          .filter((g) => !overriddenGuildIds.has(g.id))
                          .map((g) => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                          ))}
                      </select>
                    </div>
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
