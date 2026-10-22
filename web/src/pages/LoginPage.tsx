import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "@/lib/api";

// Centered sign-in card, mirroring the request-access screen: avatar, bold
// title, muted subtitle and one accent action. Discord OAuth is the only
// way in; no manual token fallback. OAuth callback failures land back here
// with ?error=... (shown once, then stripped from the URL).
export default function LoginPage() {
  const navigate = useNavigate();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(
    () => new URLSearchParams(window.location.search).get("error") ?? ""
  );

  // One-shot error from the OAuth callback: displayed, then removed from
  // the address bar so a manual refresh does not re-surface a stale one.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("error")) {
      params.delete("error");
      const qs = params.toString();
      navigate(`/login${qs ? `?${qs}` : ""}`, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startDiscordLogin() {
    setStarting(true);
    setError("");
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        "/api/auth/discord/start",
        { method: "GET" }
      );
      window.location.href = authorize_url;
    } catch (err) {
      setError((err as Error).message);
      setStarting(false);
    }
  }

  return (
    <main className="min-h-svh flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-card border border-border rounded-xl p-6">
        <header className="flex items-center gap-3 mb-5">
          <img src="/bot.png" alt="PakoDJ" className="w-10 h-10 rounded-xl" />
          <div>
            <h1 className="text-lg font-semibold">PakoDJ</h1>
            <p className="text-xs text-muted">Discord music control panel</p>
          </div>
        </header>

        <p className="text-sm text-secondary mb-5">
          Sign in with your Discord account: an admin reviews what you can see
          and control.
        </p>

        {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

        <button
          onClick={startDiscordLogin}
          disabled={starting}
          className="w-full px-4 py-2.5 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition disabled:opacity-50"
        >
          {starting ? "Redirecting..." : "Login with Discord"}
        </button>
      </div>
    </main>
  );
}
