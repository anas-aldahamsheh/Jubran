"use client";

import { AnimatePresence, motion } from "motion/react";
import { Minus, Plus, Trash2 } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";

/**
 * − value + with big touch targets; the number rolls up or down when it changes.
 * With `removeAtOne`, the minus becomes a bin at 1 (for basket lines).
 */
export function QuantityStepper({ value, onDecrement, onIncrement, decrementDisabled = false, incrementDisabled = false, size = "md", removeAtOne = false, label, className = "" }: {
  value: number;
  onDecrement: () => void;
  onIncrement: () => void;
  decrementDisabled?: boolean;
  incrementDisabled?: boolean;
  size?: "sm" | "md" | "lg";
  removeAtOne?: boolean;
  label?: string;
  className?: string;
}) {
  const { t } = useLanguage();
  const dims = size === "lg" ? "size-12" : size === "sm" ? "size-8" : "size-10";
  const icon = size === "lg" ? "size-5" : size === "sm" ? "size-3.5" : "size-4";
  const valueWidth = size === "lg" ? "w-12 text-xl" : size === "sm" ? "w-7 text-sm" : "w-9 text-base";
  const removing = removeAtOne && value <= 1;

  return (
    <div role="group" aria-label={label} className={`inline-flex items-center rounded-full border border-line bg-surface-2 p-1 ${className}`}>
      <button
        type="button"
        onClick={onDecrement}
        disabled={decrementDisabled}
        aria-label={removing ? t("حذف", "Remove") : t("إنقاص الكمية", "Decrease quantity")}
        className={`flex ${dims} items-center justify-center rounded-full transition-colors disabled:opacity-35 ${removing ? "text-danger hover:bg-danger-soft" : "text-ink-2 hover:bg-surface-3 hover:text-ink"}`}
      >
        {removing ? <Trash2 className={icon} aria-hidden="true" /> : <Minus className={icon} aria-hidden="true" />}
      </button>
      <span className={`relative flex h-full items-center justify-center overflow-hidden font-bold tabular-nums ${valueWidth}`} aria-live="polite">
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={value}
            initial={{ y: 14, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -14, opacity: 0 }}
            transition={{ type: "spring", stiffness: 520, damping: 32 }}
          >
            {value}
          </motion.span>
        </AnimatePresence>
      </span>
      <button
        type="button"
        onClick={onIncrement}
        disabled={incrementDisabled}
        aria-label={t("زيادة الكمية", "Increase quantity")}
        className={`flex ${dims} items-center justify-center rounded-full bg-brand text-on-brand shadow-glow transition-colors hover:bg-brand-hover disabled:opacity-35 disabled:shadow-none`}
      >
        <Plus className={icon} aria-hidden="true" />
      </button>
    </div>
  );
}
