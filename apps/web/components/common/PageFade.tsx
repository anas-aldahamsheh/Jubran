"use client";

import { useEffect } from "react";
import { motion } from "motion/react";
import { EASE_OUT } from "@/lib/motion";

// Per area (the whole site, the guest pages, the admin pages): the first page appears as the
// server sent it; pages opened after it fade in.
const shownBefore = new Set<string>();

/**
 * A page change inside one area: the new page fades in (opacity only, so fixed and sticky
 * parts stay put) while the area's header and tab bar stay where they are.
 */
export function PageFade({ area, children }: { area: string; children: React.ReactNode }) {
  const animate = shownBefore.has(area);
  useEffect(() => {
    shownBefore.add(area);
  }, [area]);

  return (
    <motion.div
      initial={animate ? { opacity: 0 } : false}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35, ease: EASE_OUT }}
      className="flex flex-1 flex-col"
    >
      {children}
    </motion.div>
  );
}
