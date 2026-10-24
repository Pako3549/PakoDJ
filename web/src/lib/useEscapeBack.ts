import { useLocation, useNavigate } from "react-router-dom";
import { useEffect } from "react";

// In-page navigation history: every route change pushes onto a capped stack,
// so a "back" action can return to wherever the user actually came from
// (dashboard -> guild -> users -> back lands on the guild, not on "/").
// New pages get the behaviour for free by using useEscapeBack or goBack().
const STACK_KEY = "pakodj:nav-stack";
const CAP = 50;

function load(): string[] {
  try {
    const raw = sessionStorage.getItem(STACK_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((p) => typeof p === "string") : [];
  } catch {
    return [];
  }
}

function save(stack: string[]) {
  try {
    sessionStorage.setItem(STACK_KEY, JSON.stringify(stack.slice(-CAP)));
  } catch {
    // private browsing / quota: the stack just lives shorter, harmless
  }
}

export function pushNav(path: string) {
  const stack = load();
  if (stack[stack.length - 1] === path) return;
  // Re-rooting model: visiting a page already in the stack prunes the
  // forward entries (browser-style). Without this, back/forth between two
  // pages would ping-pong forever instead of walking distinct pages.
  const idx = stack.indexOf(path);
  if (idx >= 0) {
    save(stack.slice(0, idx + 1));
    return;
  }
  stack.push(path);
  save(stack);
}

// Where "back" should land from the current path: the latest stack entry
// that differs from it (current included, since it is pushed on arrival).
export function backPath(current: string): string | null {
  const stack = load();
  for (let i = stack.length - 1; i >= 0; i--) {
    if (stack[i] !== current) return stack[i];
  }
  return null;
}

// Go back to the previous in-app page, falling back to "/" when the stack
// is empty (direct load / private window).
export function goBack(current: string): string {
  return backPath(current) ?? "/";
}

// Mount once inside the Router: records the visited paths. The OAuth
// callback is never recorded: landing on it via "back" would be a dead end.
export function NavTracker() {
  const location = useLocation();
  useEffect(() => {
    if (location.pathname.startsWith("/auth/")) return;
    pushNav(location.pathname + location.search);
  }, [location.pathname, location.search]);
  return null;
}

// Escape mirrors the header back arrow: returns to the previous in-app page.
export function useEscapeBack(enabled = true) {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      // Plain Escape only: any other key (or Ctrl/Alt/Meta combo) must
      // never trigger the in-app back navigation.
      if (e.ctrlKey || e.altKey || e.metaKey) return;
      if (e.key !== "Escape") return;
      navigate(goBack(location.pathname + location.search));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, navigate, location.pathname, location.search]);
}
