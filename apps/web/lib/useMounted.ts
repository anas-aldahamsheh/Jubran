"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/** False on the server and during hydration, true afterwards (for portals and browser-only UI). */
export function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
