"use client";

import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { CircleAlert, CircleCheck, Info } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { useMounted } from "@/lib/useMounted";

export type ToastTone = "success" | "error" | "info";

const TONES: Record<ToastTone, { icon: typeof CircleCheck; className: string }> = {
  success: { icon: CircleCheck, className: "bg-ink text-canvas" },
  error: { icon: CircleAlert, className: "bg-danger text-white" },
  info: { icon: Info, className: "bg-info text-white" },
};

/**
 * A short message that slides in at the top of the screen. Pages keep their own
 * message state and timer; this only shows it (and animates it out when it clears).
 */
export function Toast({ message, tone = "success", className = "" }: { message: string | null | undefined; tone?: ToastTone; className?: string }) {
  const { dir } = useLanguage();
  const mounted = useMounted();
  if (!mounted) return null;
  const { icon: Icon, className: toneClass } = TONES[tone];

  return createPortal(
    <div className={`pointer-events-none fixed inset-x-0 top-[max(0.75rem,env(safe-area-inset-top))] z-[150] flex justify-center px-4 ${className}`} dir={dir}>
      <AnimatePresence mode="popLayout">
        {message && (
          <motion.div
            key={message}
            role="status"
            aria-live="polite"
            initial={{ opacity: 0, y: -24, scale: 0.94, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -16, scale: 0.96, transition: { duration: 0.18 } }}
            transition={{ type: "spring", stiffness: 460, damping: 30 }}
            className={`pointer-events-auto flex max-w-[min(34rem,100%)] items-start gap-2.5 rounded-2xl px-4 py-3 text-sm font-semibold leading-relaxed shadow-float ${toneClass}`}
          >
            <Icon className="mt-0.5 size-[18px] shrink-0" aria-hidden="true" />
            <span>{message}</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>,
    document.body,
  );
}
