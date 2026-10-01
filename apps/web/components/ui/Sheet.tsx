"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useDragControls, type PanInfo } from "motion/react";
import { X } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { useModalDialog } from "@/lib/useModalDialog";
import { useMediaQuery, WIDE_QUERY } from "@/lib/useMediaQuery";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import { useMounted } from "@/lib/useMounted";
import { spring, EASE_IN, EASE_OUT } from "@/lib/motion";

const WIDTHS = {
  sm: "sm:max-w-md",
  md: "sm:max-w-lg",
  lg: "sm:max-w-2xl",
  xl: "sm:max-w-4xl",
} as const;

type SheetProps = {
  open: boolean;
  onClose: () => void;
  /** id of the heading that names the dialog */
  labelledBy?: string;
  describedBy?: string;
  size?: keyof typeof WIDTHS;
  /** "alertdialog" for confirmations the guest must answer. */
  role?: "dialog" | "alertdialog";
  className?: string;
  children: React.ReactNode;
};

/**
 * One dialog pattern for the whole app: a bottom sheet on phones (drag the handle down
 * to close) and a centred dialog on wider screens. Keyboard and screen readers get a
 * real modal (focus kept inside, Escape closes, focus returns to the opener).
 */
export function Sheet({ open, onClose, labelledBy, describedBy, size = "md", role = "dialog", className = "", children }: SheetProps) {
  const wide = useMediaQuery(WIDE_QUERY);
  const keyboard = useKeyboardInset();
  const dialogRef = useModalDialog<HTMLDivElement>(open, onClose);
  const dragControls = useDragControls();
  const { dir } = useLanguage();
  const mounted = useMounted();

  // The page behind stays still while a sheet is open.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.y > 110 || info.velocity.y > 650) onClose();
  };

  if (!mounted) return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-x-0 top-0 z-[120] flex items-end justify-center sm:items-center sm:p-6"
          style={{ bottom: keyboard }}
          dir={dir}
        >
          <motion.div
            aria-hidden="true"
            className="absolute inset-0 bg-[var(--overlay)] backdrop-blur-[3px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.18 } }}
            transition={{ duration: 0.28, ease: EASE_OUT }}
            onClick={onClose}
          />
          <motion.div
            ref={dialogRef}
            role={role}
            aria-modal="true"
            aria-labelledby={labelledBy}
            aria-describedby={describedBy}
            tabIndex={-1}
            className={`relative flex max-h-[min(92dvh,100%)] w-full flex-col overflow-hidden rounded-t-[1.75rem] border border-line bg-elevated text-ink shadow-float outline-none sm:max-h-[min(88dvh,54rem)] sm:rounded-[1.75rem] ${WIDTHS[size]} ${className}`}
            initial={wide ? { opacity: 0, scale: 0.95, y: 16 } : { y: "100%" }}
            animate={wide ? { opacity: 1, scale: 1, y: 0 } : { y: 0 }}
            exit={wide ? { opacity: 0, scale: 0.97, y: 10, transition: { duration: 0.16 } } : { y: "100%", transition: { duration: 0.22, ease: EASE_IN } }}
            transition={spring.sheet}
            drag={wide ? false : "y"}
            dragListener={false}
            dragControls={dragControls}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.65 }}
            onDragEnd={onDragEnd}
          >
            {!wide && (
              <div
                className="flex shrink-0 cursor-grab touch-none justify-center pb-1 pt-2.5 active:cursor-grabbing"
                onPointerDown={(event) => dragControls.start(event)}
                aria-hidden="true"
              >
                <span className="h-1.5 w-11 rounded-full bg-line-strong" />
              </div>
            )}
            {children}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

/** Title row with an optional subtitle and a close button. */
export function SheetHeader({ id, title, subtitle, onClose, icon, children }: {
  id?: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  onClose?: () => void;
  icon?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const { t } = useLanguage();
  return (
    <div className="flex shrink-0 items-start gap-3 px-5 pb-3 pt-2 sm:px-6 sm:pt-6">
      {icon && <span className="mt-0.5 flex size-11 shrink-0 items-center justify-center rounded-2xl bg-brand-soft text-brand-soft-ink">{icon}</span>}
      <div className="min-w-0 flex-1">
        <h2 id={id} className="font-display text-xl font-bold leading-snug text-ink sm:text-[1.375rem]">{title}</h2>
        {subtitle && <p className="mt-1 text-sm leading-relaxed text-muted">{subtitle}</p>}
        {children}
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label={t("إغلاق", "Close")}
          className="-me-1 -mt-1 flex size-10 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-3 hover:text-ink"
        >
          <X className="size-5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export function SheetBody({ className = "", flush = false, children }: { className?: string; flush?: boolean; children: React.ReactNode }) {
  return <div className={`min-h-0 flex-1 overflow-y-auto overscroll-contain ${flush ? "" : "px-5 pb-4 sm:px-6"} ${className}`}>{children}</div>;
}

export function SheetFooter({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={`flex shrink-0 items-center gap-2.5 border-t border-line bg-elevated/95 px-5 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3.5 backdrop-blur sm:px-6 sm:pb-5 ${className}`}>
      {children}
    </div>
  );
}

/** Keeps a quick fade for content swaps inside a sheet. */
export const sheetContentFade = { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.3, ease: EASE_OUT } };
