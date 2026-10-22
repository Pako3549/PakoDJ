// Instant-render helper: pages hydrate from the last successfully fetched
// data so defaults never appear before the (stale but real) values, then
// refresh in place. Cache is best-effort only: any storage error is ignored.
export function readCache<T>(key: string, ttlMs = 0): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const { ts, data } = JSON.parse(raw);
    if (ttlMs > 0 && Date.now() - ts > ttlMs) return null;
    return data as T;
  } catch {
    return null;
  }
}

export function writeCache(key: string, data: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify({ ts: Date.now(), data }));
  } catch {
    // storage unavailable/full: the cache is an optimization, not a feature
  }
}
