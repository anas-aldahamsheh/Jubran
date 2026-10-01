"use client";

import Image from "next/image";
import { motion } from "motion/react";

/** "Ask the waiter": the assistant's own face (the same as the chat bubble), online. */
export function AskAssistantButton({ label, onClick, delay = 0, className = "" }: {
  label: string;
  onClick: () => void;
  /** Entrance delay, to follow the heading it sits under. */
  delay?: number;
  className?: string;
}) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      data-assistant-trigger
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.97 }}
      // The entrance is CSS, so it shows before the page's scripts arrive.
      style={{ "--rise-delay": `${delay}s`, "--rise-from": "12px" } as React.CSSProperties}
      className={`animate-rise group inline-flex items-center gap-3 rounded-full border border-line bg-surface/90 py-1.5 pe-5 ps-1.5 text-sm font-semibold text-ink shadow-card backdrop-blur transition-[border-color,background-color,box-shadow] hover:border-brand-line hover:bg-surface hover:shadow-lift ${className}`}
    >
      <span className="relative shrink-0">
        <Image
          src="/brand/jubran-emblem.webp"
          alt=""
          width={80}
          height={80}
          className="size-9 rounded-full bg-gradient-to-br from-[#41579e] to-[#151e40] object-cover ring-2 ring-brand-line transition-transform duration-300 group-hover:scale-105"
        />
        <span className="absolute -bottom-0.5 -end-0.5 flex size-3.5 items-center justify-center rounded-full bg-surface" aria-hidden="true">
          <span className="size-2 rounded-full bg-success" />
        </span>
      </span>
      <span>{label}</span>
    </motion.button>
  );
}
