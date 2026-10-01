"use client";

import { MotionConfig } from "motion/react";

/** Motion follows the device setting: with "reduce motion", movement becomes a plain fade. */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
