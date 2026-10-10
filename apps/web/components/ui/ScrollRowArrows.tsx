"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import type { ScrollRowEdges } from "@/lib/useScrollRow";

/**
 * Arrow buttons at both ends of a horizontal row (useScrollRow), shown only while
 * there is more to see that way. Place inside a `relative` parent of the row.
 * Phones swipe, so the arrows appear from tablet width up.
 */
export function ScrollRowArrows({ edges, onStep, className = "" }: {
  edges: ScrollRowEdges;
  onStep: (direction: 1 | -1) => void;
  className?: string;
}) {
  const { dir, t } = useLanguage();
  const rtl = dir === "rtl";
  const button = (side: "start" | "end") => {
    const visible = edges[side];
    const pointsRight = (side === "end") !== rtl;
    const Icon = pointsRight ? ChevronRight : ChevronLeft;
    return (
      <button
        type="button"
        tabIndex={-1}
        aria-hidden={!visible}
        onClick={() => onStep(side === "end" ? 1 : -1)}
        aria-label={side === "end" ? t("الأقسام التالية", "Next sections") : t("الأقسام السابقة", "Previous sections")}
        className={`absolute top-1/2 z-10 hidden size-9 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-surface/95 text-ink shadow-lift backdrop-blur transition-[opacity,transform] duration-200 hover:scale-105 hover:border-brand-line hover:text-brand md:flex ${
          side === "start" ? "start-1.5" : "end-1.5"
        } ${visible ? "opacity-100" : "pointer-events-none scale-90 opacity-0"} ${className}`}
      >
        <Icon className="size-[18px]" aria-hidden="true" />
      </button>
    );
  };
  return (
    <>
      {button("start")}
      {button("end")}
    </>
  );
}
