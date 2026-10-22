import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { setToken } from "@/lib/api";

// OAuth landing: the bot API redirects here after a successful Discord login,
// with the session data in the URL fragment (never sent to any server).
export default function OauthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("session_token");
    if (token) {
      setToken(token);
      // Full document load instead of SPA navigation: the dashboard mounts
      // on a clean location (no router state carried over from the OAuth
      // redirect), and the URL fragment stays out of the address bar.
      window.location.replace("/");
    } else {
      navigate("/login", { replace: true });
    }
  }, [navigate]);

  return (
    <main className="max-w-md mx-auto px-4 py-16">
      <div className="bg-card border border-border rounded-xl p-6 text-center">
        <p className="text-sm text-secondary">Signing you in...</p>
      </div>
    </main>
  );
}
