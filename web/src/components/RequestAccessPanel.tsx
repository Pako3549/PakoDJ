import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Account } from "@/lib/auth";
import ErrorBox from "@/components/ErrorBox";

type SharedGuild = { id: string; name: string };

// Shown when the account has no `view` permission: onboarding screen that
// lets the user request access per shared guild (admins resolve the queue).
export default function RequestAccessPanel({ account }: { account: Account }) {
  const [guilds, setGuilds] = useState<SharedGuild[] | null>(null);
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  useEffect(() => {
    let alive = true;
    apiFetch<SharedGuild[]>("/api/access/guilds")
      .then((gs) => {
        if (!alive) return;
        setGuilds(gs);
        // Only pending asks are reported by the API (409 on duplicates);
        // knowing which are already asked needs no extra endpoint.
      })
      .catch(() => alive && setGuilds([]));
    return () => {
      alive = false;
    };
  }, [account.id]);

  const request = useCallback(
    async (guild: SharedGuild) => {
      setError("");
      setInfo("");
      try {
        await apiFetch("/api/access-requests", {
          method: "POST",
          body: JSON.stringify({ guild_id: guild.id, message: message.trim() }),
        });
        setRequested((prev) => new Set([...prev, guild.id]));
        setInfo(`Request for ${guild.name} sent.`);
      } catch (err) {
        if (err instanceof Error && /pending/i.test(err.message)) {
          setRequested((prev) => new Set([...prev, guild.id]));
          setInfo(`A request for ${guild.name} is already pending.`);
        } else {
          setError((err as Error).message);
        }
      }
    },
    [message]
  );

  return (
    <main className="max-w-lg mx-auto px-4 py-12">
      <header className="flex items-center gap-3 mb-6">
        <img src="/bot.png" alt="PakoDJ" className="w-10 h-10 rounded-xl" />
        <div>
          <h1 className="text-lg font-semibold">Request access</h1>
          <p className="text-xs text-muted">
            signed in as {account.username}
          </p>
        </div>
      </header>

      <p className="text-sm text-secondary mb-4">
        Your account has no permissions yet. Ask for the servers you need: an
        admin reviews the request and grants the right permissions.
      </p>

      {error && <ErrorBox message={error} />}
      {info && <p className="text-xs text-accent mb-3">{info}</p>}

      {guilds === null ? (
        <p className="text-xs text-muted">Loading shared servers...</p>
      ) : guilds.length === 0 ? (
        <p className="text-xs text-muted">
          No shared servers. Join a server with the bot and log in again.
        </p>
      ) : (
        <div className="space-y-2">
          {guilds.map((g) => (
            <div
              key={g.id}
              className="flex items-center gap-3 bg-card border border-border rounded-lg p-3"
            >
              <span className="flex-1 text-sm text-primary truncate">{g.name}</span>
              {requested.has(g.id) ? (
                <span className="text-xs text-muted px-3 py-1.5 rounded-lg border border-border">
                  requested
                </span>
              ) : (
                <button
                  onClick={() => request(g)}
                  className="px-3 py-1.5 rounded-lg bg-accent text-inverse text-xs font-medium hover:bg-accent/90 transition"
                >
                  Request access
                </button>
              )}
            </div>
          ))}
          <label className="block mt-4 text-xs text-muted">
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
        </div>
      )}
    </main>
  );
}
