"use client";

import { motion } from "motion/react";
import { LoaderCircle } from "lucide-react";
import { spring } from "@/lib/motion";

/**
 * An on/off switch whose knob springs across (works in both reading directions). While the
 * change is on its way (`busy`), a small circle turns inside the knob and taps wait.
 */
export function Switch({ checked, onChange, disabled = false, busy = false, label, size = "md" }: {
  checked: boolean;
  onChange: (next: boolean, event: React.MouseEvent) => void;
  disabled?: boolean;
  busy?: boolean;
  label: string;
  size?: "sm" | "md";
}) {
  const track = size === "sm" ? "h-6 w-10" : "h-7 w-12";
  const knob = size === "sm" ? "size-5" : "size-6";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-busy={busy}
      aria-label={label}
      title={label}
      disabled={disabled || busy}
      onClick={(event) => onChange(!checked, event)}
      className={`relative inline-flex shrink-0 items-center rounded-full p-0.5 transition-colors duration-200 ${disabled && !busy ? "opacity-50" : ""} ${track} ${checked ? "justify-end bg-brand" : "justify-start bg-line-strong"}`}
    >
      <motion.span layout transition={spring.snappy} className={`flex items-center justify-center rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.25)] ${knob}`}>
        {busy && <LoaderCircle className="size-3 animate-spin text-brand" aria-hidden="true" />}
      </motion.span>
    </button>
  );
}
