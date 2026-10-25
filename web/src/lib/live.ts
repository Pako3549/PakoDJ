import { useEffect, useRef } from "react";
import { getToken } from "@/lib/api";

// Raw reader: one streaming connection with the session token that fires
// onEvent for every pushed event (except the connect handshake) and
// RECONNECTS with a short backoff forever — a bot restart or a dropped
// connection must never leave a page deaf until a manual reload. Pages never
// call this directly: they subscribe to the shared singleton below.
function rawOpenStream(onEvent: (event: string) => void): () => void {
  let ctrl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const connect = () => {
    if (stopped) return;
    const token = getToken();
    if (!token) {
      // Logged out: retry slowly, a later login must still get live events.
      timer = setTimeout(connect, 5000);
      return;
    }
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

// Shared singleton: ONE connection per tab, N subscribers. Every hook used
// to open its own stream (the guild page totaled three with the account
// watcher) — a single multiplexed reader cuts the overhead of all those
// parallel decoders and reconnect timers.
type Listener = (event: string) => void;
const listeners = new Set<Listener>();
let closeRaw: (() => void) | null = null;

function broadcast(name: string) {
  for (const listen of [...listeners]) {
    try {
      listen(name);
    } catch {
      // one misbehaving subscriber must not starve the others
    }
  }
}

export function openStream(onEvent: Listener): () => void {
  if (!closeRaw) closeRaw = rawOpenStream(broadcast);
  listeners.add(onEvent);
  return () => {
    listeners.delete(onEvent);
    // Last subscriber out: tear the connection down until someone needs it.
    if (listeners.size === 0) {
      closeRaw?.();
      closeRaw = null;
    }
  };
}

// Hook flavour for pages: stable handler via ref, cleanup on unmount.
export function useStreamEvents(handler: (event: string) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => openStream((name) => ref.current(name)), []);
}
