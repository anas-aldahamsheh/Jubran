"use client";

import { AnimatePresence, motion } from "motion/react";
import { CloudOff, LoaderCircle, RefreshCw } from "lucide-react";
import { EASE_OUT } from "@/lib/motion";
import { useLanguage } from "@/context/LanguageContext";

/** A calm placeholder shape while content loads. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function Spinner({ className = "size-5", label }: { className?: string; label?: string }) {
  return (
    <span role={label ? "status" : undefined} className="inline-flex items-center">
      <LoaderCircle className={`animate-spin ${className}`} aria-hidden="true" />
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}

/** The small turning circle inside a button whose action is on its way. */
export function ButtonSpinner({ className = "size-4" }: { className?: string }) {
  return <LoaderCircle className={`shrink-0 animate-spin ${className}`} aria-hidden="true" />;
}

/**
 * Content that loads: its placeholder shows until it is ready, then melts away while the
 * content rises into its place (both overlap for a moment, so nothing jumps or flashes).
 * Content that is ready at once simply shows.
 */
export function Reveal({ ready, skeleton, children, className = "", label }: {
  ready: boolean;
  skeleton: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** Read out while loading (e.g. "Loading the menu…"). */
  label?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <AnimatePresence mode="popLayout" initial={false}>
        {ready ? (
          <motion.div
            key="content"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: EASE_OUT }}
          >
            {children}
          </motion.div>
        ) : (
          <motion.div
            key="skeleton"
            exit={{ opacity: 0, scale: 0.985, transition: { duration: 0.25, ease: EASE_OUT } }}
            role="status"
            aria-label={label}
            aria-busy="true"
          >
            {skeleton}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * A wait that runs long (a slow connection): after a few seconds a calm line says we are still
 * on it. Shown only while it is mounted, so it disappears the moment the content arrives.
 */
export function SlowNote({ after = 6, className = "" }: { after?: number; className?: string }) {
  const { t } = useLanguage();
  return (
    <p
      className={`animate-late-in flex items-center justify-center gap-2 text-xs font-medium text-muted ${className}`}
      style={{ "--late-delay": `${after}s` } as React.CSSProperties}
      role="status"
    >
      <span className="relative flex size-2" aria-hidden="true">
        <span className="absolute inset-0 animate-ping rounded-full bg-brand/60" />
        <span className="relative size-2 rounded-full bg-brand" />
      </span>
      {t("الاتصال بطيء شوي… لحظات ويكون جاهز", "The connection is a little slow… almost there")}
    </p>
  );
}

/**
 * Loading failed: say so plainly and offer a button. The page keeps trying by itself too
 * (see useAutoRetry), so the guest usually only sees it come back.
 */
export function LoadError({ title, onRetry, retrying = false, className = "" }: {
  title?: string;
  onRetry: () => void;
  retrying?: boolean;
  className?: string;
}) {
  const { t } = useLanguage();
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: EASE_OUT }}
      className={`flex flex-col items-center justify-center rounded-3xl border border-dashed border-line-strong bg-surface-2/60 px-6 py-9 text-center ${className}`}
      role="alert"
    >
      <span className="relative mb-4 flex size-14 items-center justify-center rounded-3xl bg-surface text-muted shadow-card">
        <CloudOff className="size-7" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <h3 className="font-display text-lg font-bold text-ink">{title ?? t("ما قدرنا نحمّل هاد الجزء", "We couldn't load this part")}</h3>
      <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-muted">
        {t("غالباً الاتصال انقطع لحظة. رح نعيد المحاولة لحالنا، أو اضغط الزر.", "The connection probably dropped for a moment. We'll keep trying, or tap the button.")}
      </p>
      <button type="button" onClick={onRetry} disabled={retrying} className="btn btn-secondary btn-sm mt-5" aria-busy={retrying}>
        {retrying ? <ButtonSpinner /> : <RefreshCw className="size-4" aria-hidden="true" />}
        {retrying ? t("جاري المحاولة…", "Trying…") : t("إعادة المحاولة", "Try again")}
      </button>
    </motion.div>
  );
}

/** Nothing to show yet: an icon, a sentence and one way forward. */
export function EmptyState({ icon, title, description, action, className = "" }: {
  icon: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: EASE_OUT }}
      className={`flex flex-col items-center justify-center rounded-3xl border border-dashed border-line-strong bg-surface-2/60 px-6 py-10 text-center ${className}`}
    >
      <span className="relative mb-4 flex size-16 items-center justify-center rounded-3xl bg-surface text-brand shadow-card">
        <span className="absolute inset-0 rounded-3xl bg-brand/5" aria-hidden="true" />
        {icon}
      </span>
      <h3 className="font-display text-lg font-bold text-ink">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </motion.div>
  );
}
