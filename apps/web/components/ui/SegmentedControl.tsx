"use client";

import { useId } from "react";
import { motion } from "motion/react";
import { spring } from "@/lib/motion";

export type Segment<T extends string> = { value: T; label: React.ReactNode; count?: number; icon?: React.ReactNode };

/**
 * Tabs or filters in one pill: the highlight glides to the chosen segment.
 * `stretch` makes the segments share the full width on phones.
 */
export function SegmentedControl<T extends string>({ value, onChange, segments, ariaLabel, stretch = false, size = "md", className = "" }: {
  value: T;
  onChange: (value: T) => void;
  segments: Segment<T>[];
  ariaLabel: string;
  stretch?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  const groupId = useId();
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={`relative items-stretch gap-1 rounded-2xl border border-line bg-surface-3/70 p-1 ${stretch ? "flex w-full sm:inline-flex sm:w-auto" : "inline-flex"} ${className}`}
    >
      {segments.map((segment) => {
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            onClick={() => onChange(segment.value)}
            aria-pressed={active}
            className={`relative isolate inline-flex min-w-0 items-center justify-center gap-1.5 rounded-xl font-semibold transition-colors ${size === "sm" ? "min-h-8 px-2.5 text-xs" : "min-h-10 px-3.5 text-sm"} ${stretch ? "flex-1 sm:flex-none" : ""} ${active ? "text-ink" : "text-muted hover:text-ink"}`}
          >
            {active && (
              <motion.span
                layoutId={`segment-${groupId}`}
                className="absolute inset-0 -z-10 rounded-xl border border-line bg-surface shadow-card"
                transition={spring.snappy}
              />
            )}
            {segment.icon}
            <span className="truncate">{segment.label}</span>
            {segment.count !== undefined && (
              <span className={`min-w-5 rounded-full px-1.5 py-0.5 text-[0.6875rem] font-bold tabular-nums leading-none ${active ? "bg-brand text-on-brand" : "bg-line text-ink-2"}`}>
                {segment.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
