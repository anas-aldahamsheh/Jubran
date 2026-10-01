"use client";

import { useSyncExternalStore } from "react";

/**
 * True while the media query matches. The server (and the first render in the browser)
 * assumes it does not match, so layouts built for phones first never mismatch on hydration.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Tablets in landscape and larger screens: side-by-side layouts. */
export const DESKTOP_QUERY = "(min-width: 1024px)";
/** Everything wider than a phone: dialogs instead of bottom sheets. */
export const WIDE_QUERY = "(min-width: 640px)";
