import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, ApiError } from "@/lib/api";
import { useAccount } from "@/lib/auth";
import ThemeToggle from "@/components/ThemeToggle";
import ErrorBox from "@/components/ErrorBox";

type User = {
  id: number;
  username: string;
  role: string;
  seed: boolean;
  discord_id: string;
};

type AccessRequest = {
  id: number;
  user_id: number;
  username: string;
  guild_id: number;
  guild_name: string;
  message: string;
  created_at: string;
};

const ROLES = ["nobody", "viewer", "admin"] as const;

export default function UsersPage() {
  const { account, loading, error: authError } = useAccount(true);
  const [users, setUsers] = useState<User[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const [us, rs] = await Promise.all([
        apiFetch<User[]>("/api/users"),
        apiFetch<AccessRequest[]>("/api/access-requests"),
      ]);
      setUsers(us);
      setRequests(rs);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (account) reload();
  }, [account, reload]);

  if (loading || !account) return authError ? <ErrorBox message={authError} /> : null;

  async function changeRole(userId: number, role: string) {
    setBusy(userId);
    setError("");
    try {
      await apiFetch(`/api/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      await reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function resolveRequest(requestId: number, status: "granted" | "denied") {
    setBusy(requestId);
    setError("");
    try {
      await apiFetch(`/api/access-requests/${requestId}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status !== 404) setError(err.message);
      else await reload();
    } finally {
      setBusy(null);
    }
  }

  const myId = account.id;

  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <Link to="/" className="px-3 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition">
            ←
          </Link>
          <div>
            <h1 className="text-lg font-semibold">Users</h1>
            <p className="text-xs text-muted">
              {requests.length > 0 ? `${requests.length} pending access request(s)` : "no pending requests"}
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      {error && <p className="text-xs text-red-400 mb-4">{error}</p>}

      {requests.length > 0 && (
        <section className="bg-card border border-border rounded-xl mb-6">
          <div className="px-4 py-3 border-b border-border">
            <h2 className="text-sm font-medium text-accent">Access requests</h2>
          </div>
          <div className="p-4 space-y-2">
            {requests.map((r) => (
              <div key={r.id} className="flex items-center gap-3 bg-surface border border-border rounded-lg p-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-primary truncate">
                    {r.username} <span className="text-muted">→ {r.guild_name || r.guild_id}</span>
                  </p>
                  {r.message && <p className="text-xs text-secondary truncate">{r.message}</p>}
                </div>
                <button
                  onClick={() => changeRole(r.user_id, "viewer").then(() => resolveRequest(r.id, "granted"))}
                  disabled={busy !== null}
                  className="px-3 py-1 rounded bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition disabled:opacity-50"
                >
                  Grant (viewer)
                </button>
                <button
                  onClick={() => resolveRequest(r.id, "denied")}
                  disabled={busy !== null}
                  className="px-3 py-1 rounded bg-card border border-border text-xs text-secondary hover:text-primary transition disabled:opacity-50"
                >
                  Deny
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-medium">Accounts ({users.length})</h2>
        </div>
        <div className="p-4 space-y-2">
          {users.map((u) => (
            <div key={u.id} className="flex items-center gap-3 bg-surface border border-border rounded-lg p-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm text-primary truncate flex items-center gap-2">
                  {u.username}
                  {u.id === myId && <span className="text-xs text-muted">(you)</span>}
                  {u.seed && (
                    <span className="text-xs px-2 py-0.5 rounded bg-accent/20 text-accent" title="Managed by DISCORD_ADMIN_IDS; cannot be changed">
                      🔒 seed
                    </span>
                  )}
                </p>
                <p className="text-xs text-muted font-mono truncate">{u.discord_id}</p>
              </div>
              <select
                value={u.role}
                disabled={u.seed || busy !== null}
                onChange={(e) => changeRole(u.id, e.target.value)}
                className="px-2 py-1 rounded bg-card border border-border text-sm text-primary disabled:opacity-50"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
