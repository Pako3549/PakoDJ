import { useEffect, useRef } from "react";
import { getToken } from "@/lib/api";

// Single-purpose SSE reader over the shared update stream (same connection
// pattern as the auth watchers): fires `handler` with every pushed event
// name except the connect/keepalive noise. No polling loops anywhere: the
// server holds the connection open and pushes when something changes.
export function useStreamEvents(handler: (event: string) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const ctrl = new AbortController();
    (async () => {
      const res = await fetch("/api/stream/updates", {
        headers: { Authorization: `Bearer ${token}` },
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return; // drop: the next full load refreshes the state
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const chunk of parts) {
          if (!chunk.startsWith("event: ")) continue;
          const name = chunk.slice(7).split("\n")[0].trim();
          if (name && name !== "connected") ref.current(name);
        }
      }
    })().catch(() => { /* aborted on unmount */ });
    return () => ctrl.abort();
  }, []);
}
