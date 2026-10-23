import { useEffect, useRef } from "react";
import { getToken } from "@/lib/api";

// Shared SSE reader over the update stream: connects with the session token,
// fires onEvent with every pushed event name (except the connect handshake)
// and RECONNECTS with a short backoff forever — a bot restart or a dropped
// connection must never leave a page deaf until a manual reload. Returns the
// cleanup function.
export function openStream(onEvent: (event: string) => void): () => void {
  let ctrl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const connect = () => {
    if (stopped) return;
    const token = getToken();
    if (!token) return; // logged out: nothing to listen to
    ctrl = new AbortController();
    fetch("/api/stream/updates", {
      headers: { Authorization: `Bearer ${token}` },
      signal: ctrl.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) throw new Error("stream unavailable");
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? "";
          for (const chunk of parts) {
            if (!chunk.startsWith("event: ")) continue;
            const name = chunk.slice(7).split("\n")[0].trim();
            if (name && name !== "connected") onEvent(name);
          }
        }
      })
      .catch(() => {})
      .finally(() => {
        // Reconnect regardless of how the stream ended (bot restart, proxy
        // hiccup, network blip): live updates must self-heal.
        if (!stopped) timer = setTimeout(connect, 2000);
      });
  };
  connect();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    ctrl?.abort();
  };
}

// Hook flavour for pages: stable handler via ref, cleanup on unmount.
export function useStreamEvents(handler: (event: string) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => openStream((name) => ref.current(name)), []);
}
