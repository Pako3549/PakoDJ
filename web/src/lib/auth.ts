import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, ApiError, getToken, clearToken } from "@/lib/api";

export type Account = {
  id: number;
  username: string;
  permissions: string[];
  avatar_url: string | null;
};

export const PERMISSIONS = [
  "view",
  "search",
  "queue",
  "queue_manage",
  "playback",
  "manage_users",
] as const;

// Loads the current account once. A 401 means "really not authenticated"
// (expired/revoked session): clear token and go to /login. Any other error
// (bot down, network, 5xx) must NOT bounce: the page shows an error instead,
// otherwise login page and dashboard would ping-pong into a blank screen.
// When requirePermission is set, an account without that permission is sent
// back to the dashboard (403) instead of the login page.
export function useAccount(requirePermission?: string) {
  const navigate = useNavigate();
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    if (!getToken()) {
      navigate("/login", { replace: true });
      return;
    }
    apiFetch<Account>("/api/auth/me")
      .then((me) => {
        if (!alive) return;
        if (requirePermission && !me.permissions.includes(requirePermission)) {
          navigate("/", { replace: true });
        } else {
          setAccount(me);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          navigate("/login", { replace: true });
        } else {
          setError((err as Error).message || "API unreachable");
          setLoading(false);
        }
      });
    const onUnauthorized = () => navigate("/login", { replace: true });
    window.addEventListener("pakodj:unauthorized", onUnauthorized);
    return () => {
      alive = false;
      window.removeEventListener("pakodj:unauthorized", onUnauthorized);
    };
  }, [navigate, requirePermission]);

  return { account, loading, error };
}

export default useAccount;
