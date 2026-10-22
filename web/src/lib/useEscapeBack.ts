import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

// Escape mirrors the header back arrow (the one that goes to "/"):
// enabled only on pages that actually show such an arrow.
export function useEscapeBack(enabled = true) {
  const navigate = useNavigate();
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") navigate("/");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, navigate]);
}
