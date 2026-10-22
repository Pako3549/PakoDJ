import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, getToken, setToken } from "@/lib/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [manualToken, setManualToken] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  // A session token in the query string is the manual-fallback path of the
  // OAuth flow; with no token, an existing valid session jumps to the panel.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) {
      setToken(urlToken);
      navigate("/", { replace: true });
      return;
    }
    if (getToken()) navigate("/", { replace: true });
  }, [navigate]);

  async function startDiscordLogin() {
    setStarting(true);
    setError("");
    setInfo("");
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

  function loginWithToken(e: React.FormEvent) {
    e.preventDefault();
    const token = manualToken.trim();
    if (!token) {
      setError("Paste the session token");
      return;
    }
    setToken(token);
    navigate("/", { replace: true });
  }

  return (
    <main className="max-w-md mx-auto px-4 py-16">
      <div className="bg-card border border-border rounded-xl p-6">
        <h1 className="text-lg font-semibold mb-1">PakoDJ</h1>
        <p className="text-xs text-muted mb-6">Sign in with your Discord account</p>

        <button
          onClick={startDiscordLogin}
          disabled={starting}
          className="w-full px-4 py-2 rounded-lg bg-accent text-inverse text-sm font-medium hover:bg-accent/90 transition disabled:opacity-50"
        >
          {starting ? "Redirecting..." : "Login with Discord"}
        </button>

        {error && <p className="text-xs text-red-400 mt-3">{error}</p>}
        {info && <p className="text-xs text-secondary mt-3">{info}</p>}

        <div className="border-t border-border my-6" />

        <p className="text-xs text-muted mb-2">
          Manual fallback: if Discord redirected you back to a JSON page, copy
          the <code className="text-secondary">session_token</code> value and paste it here.
        </p>
        <form onSubmit={loginWithToken} className="flex gap-2">
          <input
            type="text"
            value={manualToken}
            onChange={(e) => setManualToken(e.target.value)}
            placeholder="session_token"
            className="flex-1 px-3 py-2 rounded-lg bg-surface border border-border text-sm text-primary placeholder:text-muted focus:outline-none focus:border-accent/60"
          />
          <button
            type="submit"
            className="px-4 py-2 rounded-lg bg-card border border-border text-sm text-secondary hover:text-primary transition"
          >
            Enter
          </button>
        </form>
      </div>
    </main>
  );
}
