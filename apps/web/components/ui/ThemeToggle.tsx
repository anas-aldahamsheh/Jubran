"use client";

import { AnimatePresence, motion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/context/ThemeContext";
import { useLanguage } from "@/context/LanguageContext";

/** Light ↔ dark. The new theme spreads from the button as a circle. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const { resolved, toggle } = useTheme();
  const { t } = useLanguage();
  const dark = resolved === "dark";
  const label = dark ? t("الوضع الفاتح", "Light mode") : t("الوضع الليلي", "Dark mode");

  return (
    <button
      type="button"
      onClick={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        toggle({ x: box.left + box.width / 2, y: box.top + box.height / 2 });
      }}
      aria-label={label}
      title={label}
      className={`relative inline-flex size-10 items-center justify-center overflow-hidden rounded-full border border-line bg-surface text-ink-2 shadow-hairline transition-colors hover:bg-surface-3 hover:text-ink ${className}`}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={dark ? "moon" : "sun"}
          initial={{ opacity: 0, rotate: -80, scale: 0.4, y: 6 }}
          animate={{ opacity: 1, rotate: 0, scale: 1, y: 0 }}
          exit={{ opacity: 0, rotate: 80, scale: 0.4, y: -6 }}
          transition={{ type: "spring", stiffness: 420, damping: 24 }}
          className="flex items-center justify-center"
        >
          {dark ? <Moon className="size-[18px]" aria-hidden="true" /> : <Sun className="size-[18px]" aria-hidden="true" />}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
