"use client";

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  const viewport = window.visualViewport;
  if (!viewport) return () => {};
  viewport.addEventListener("resize", onChange);
  viewport.addEventListener("scroll", onChange);
  return () => {
    viewport.removeEventListener("resize", onChange);
    viewport.removeEventListener("scroll", onChange);
  };
}

function coveredAtBottom(): number {
  const viewport = window.visualViewport;
  if (!viewport) return 0;
  // What the on-screen keyboard covers at the bottom of the page (iOS keeps the page
  // height and overlays the keyboard; Android resizes, which leaves 0 here).
  const covered = window.innerHeight - viewport.height - viewport.offsetTop;
  return covered > 80 ? Math.round(covered) : 0;
}

/** Height of the on-screen keyboard over the page, so bottom sheets can sit above it. */
export function useKeyboardInset(): number {
  return useSyncExternalStore(subscribe, coveredAtBottom, () => 0);
}

