"use client";

import { useEffect, useRef } from "react";

/** Waits between attempts: short at first, then calmer (the last one repeats). */
const RETRY_DELAYS_MS = [1500, 3000, 5000, 8000, 12000, 20000];

/**
 * A load that failed tries again by itself: after a wait that grows with each failure, at
 * once when the connection comes back, and when the guest returns to the tab.
 * `failures` counts the failed attempts in a row (0 = nothing to retry).
 */
export function useAutoRetry(failures: number, retry: () => void): void {
  const retryRef = useRef(retry);
  useEffect(() => {
    retryRef.current = retry;
  });

  useEffect(() => {
    if (failures <= 0) return;
    const delay = RETRY_DELAYS_MS[Math.min(failures - 1, RETRY_DELAYS_MS.length - 1)];
    const timer = window.setTimeout(() => retryRef.current(), delay);
    const now = () => {
      window.clearTimeout(timer);
      retryRef.current();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") now();
    };
    window.addEventListener("online", now);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("online", now);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [failures]);
}
