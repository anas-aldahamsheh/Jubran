"use client";

import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { THEME_COOKIE, type ThemePreference } from "@/lib/themeCookie";

type ResolvedTheme = "light" | "dark";

interface ThemeContextType {
  /** What the guest chose: follow the device, or always light / dark. */
  preference: ThemePreference;
  /** What is on screen now. */
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference, origin?: { x: number; y: number }) => void;
  /** Light ↔ dark from what is showing now (the circle grows from `origin`). */
  toggle: (origin?: { x: number; y: number }) => void;
}

const ThemeContext = createContext<ThemeContextType>({
  preference: "system",
  resolved: "light",
  setPreference: () => {},
  toggle: () => {},
});

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribeToDevice(onChange: () => void) {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function applyToDocument(preference: ThemePreference) {
  const root = document.documentElement;
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
  document.cookie = `${THEME_COOKIE}=${preference}; path=/; max-age=31536000; samesite=lax`;
}

export function ThemeProvider({ initialPreference = "system", children }: {
  initialPreference?: ThemePreference;
  children: React.ReactNode;
}) {
  const [preference, setPreferenceState] = useState<ThemePreference>(initialPreference);
  const deviceIsDark = useSyncExternalStore(subscribeToDevice, () => window.matchMedia(DARK_QUERY).matches, () => false);
  const resolved: ResolvedTheme = preference === "system" ? (deviceIsDark ? "dark" : "light") : preference;

  const setPreference = useCallback((next: ThemePreference, origin?: { x: number; y: number }) => {
    const apply = () => {
      flushSync(() => setPreferenceState(next));
      applyToDocument(next);
    };
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!origin || reduceMotion || typeof document.startViewTransition !== "function") {
      apply();
      return;
    }
    // The new theme spreads as a circle from where the guest tapped.
    const radius = Math.hypot(Math.max(origin.x, window.innerWidth - origin.x), Math.max(origin.y, window.innerHeight - origin.y));
    const transition = document.startViewTransition(apply);
    transition.ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${origin.x}px ${origin.y}px)`, `circle(${radius}px at ${origin.x}px ${origin.y}px)`] },
        { duration: 520, easing: "cubic-bezier(0.2, 0, 0, 1)", pseudoElement: "::view-transition-new(root)" },
      );
    }).catch(() => undefined);
  }, []);

  const value = useMemo<ThemeContextType>(() => ({
    preference,
    resolved,
    setPreference,
    toggle: (origin) => setPreference(resolved === "dark" ? "light" : "dark", origin),
  }), [preference, resolved, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
