"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Info, TriangleAlert } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { spring } from "@/lib/motion";

type DialogOptions = {
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};

export type DialogChoice = {
  value: string;
  label: string;
  tone?: "default" | "danger";
};

type DialogRequest = {
  id: number;
  kind: "alert" | "confirm" | "choice";
  message: string;
  options: DialogOptions;
  choices: DialogChoice[];
  /** "confirm" for the main button, a choice value, or null when dismissed. */
  resolve: (value: string | null) => void;
};

type GlobalDialogApi = {
  showAlert: (message: string, options?: DialogOptions) => Promise<void>;
  showConfirm: (message: string, options?: DialogOptions) => Promise<boolean>;
  /** Several possible answers; resolves with the chosen value, or null if dismissed. */
  showChoice: (message: string, choices: DialogChoice[], options?: DialogOptions) => Promise<string | null>;
};

const GlobalDialogContext = createContext<GlobalDialogApi | null>(null);

export function useGlobalDialog() {
  const context = useContext(GlobalDialogContext);
  if (!context) throw new Error("useGlobalDialog must be used within GlobalDialogProvider");
  return context;
}

export function GlobalDialogProvider({ children }: { children: React.ReactNode }) {
  const { dir, t } = useLanguage();
  const [active, setActive] = useState<DialogRequest | null>(null);
  const activeRef = useRef<DialogRequest | null>(null);
  const queue = useRef<DialogRequest[]>([]);
  const nextId = useRef(0);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  const activate = useCallback((request: DialogRequest | null) => {
    activeRef.current = request;
    setActive(request);
  }, []);

  const enqueue = useCallback((kind: DialogRequest["kind"], message: string, options: DialogOptions, choices: DialogChoice[] = []) => {
    return new Promise<string | null>((resolve) => {
      const request = { id: ++nextId.current, kind, message, options, choices, resolve };
      if (!activeRef.current) activate(request);
      else queue.current.push(request);
    });
  }, [activate]);

  const showAlert = useCallback(async (message: string, options?: DialogOptions) => {
    await enqueue("alert", message, options ?? {});
  }, [enqueue]);

  const showConfirm = useCallback(async (message: string, options?: DialogOptions) => {
    return (await enqueue("confirm", message, options ?? {})) === "confirm";
  }, [enqueue]);

  const showChoice = useCallback((message: string, choices: DialogChoice[], options?: DialogOptions) => {
    return enqueue("choice", message, options ?? {}, choices);
  }, [enqueue]);

  const close = useCallback((value: string | null) => {
    const current = activeRef.current;
    if (!current) return;
    current.resolve(value);
    activate(queue.current.shift() ?? null);
  }, [activate]);

  useEffect(() => {
    if (!active) return;
    confirmButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(null);
      if (event.key === "Enter" && active.kind === "alert") close("confirm");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, close]);

  const danger = active?.options.tone === "danger";

  return (
    <GlobalDialogContext.Provider value={{ showAlert, showConfirm, showChoice }}>
      {children}
      <AnimatePresence>
        {active && (
          <div
            key="global-dialog"
            className="fixed inset-0 z-[200] flex items-end justify-center p-3 sm:items-center sm:p-4"
            onMouseDown={(event) => { if (event.target === event.currentTarget) close(null); }}
          >
            <motion.div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 bg-[var(--overlay)] backdrop-blur-[3px]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.22 }}
            />
            <motion.section
              key={active.id}
              role="alertdialog"
              aria-modal="true"
              aria-labelledby={`global-dialog-title-${active.id}`}
              aria-describedby={`global-dialog-message-${active.id}`}
              initial={{ opacity: 0, scale: 0.92, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10, transition: { duration: 0.15 } }}
              transition={spring.sheet}
              className="relative w-full max-w-md overflow-hidden rounded-[1.75rem] border border-line bg-elevated text-ink shadow-float"
              dir={dir}
            >
              <div className="flex items-start gap-3.5 px-5 pb-4 pt-5">
                <motion.span
                  initial={{ scale: 0.5, rotate: -20 }}
                  animate={{ scale: 1, rotate: 0 }}
                  transition={spring.pop}
                  className={`flex size-11 shrink-0 items-center justify-center rounded-2xl ${danger ? "bg-danger-soft text-danger" : "bg-brand-soft text-brand-soft-ink"}`}
                  aria-hidden="true"
                >
                  {danger ? <TriangleAlert className="size-5" /> : <Info className="size-5" />}
                </motion.span>
                <div className="min-w-0 flex-1">
                  <h2 id={`global-dialog-title-${active.id}`} className="font-display text-lg font-bold leading-snug text-ink">
                    {active.options.title ?? (active.kind === "confirm" ? t("تأكيد الإجراء", "Please confirm") : t("تنبيه", "Notice"))}
                  </h2>
                  <p id={`global-dialog-message-${active.id}`} className="mt-1.5 whitespace-pre-wrap text-sm leading-7 text-ink-2">{active.message}</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-surface-2 px-5 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3.5 sm:pb-4">
                {active.kind !== "alert" && (
                  <button type="button" onClick={() => close(null)} className="btn btn-secondary btn-sm">
                    {active.options.cancelLabel ?? t("إلغاء", "Cancel")}
                  </button>
                )}
                {active.kind === "choice" ? active.choices.map((choice, index) => (
                  <button key={choice.value} ref={index === 0 ? confirmButtonRef : undefined} type="button" onClick={() => close(choice.value)} className={`btn btn-sm ${choice.tone === "danger" ? "btn-danger" : "btn-primary"}`}>
                    {choice.label}
                  </button>
                )) : (
                  <button ref={confirmButtonRef} type="button" onClick={() => close("confirm")} className={`btn btn-sm px-5 ${danger ? "btn-danger" : "btn-primary"}`}>
                    {active.options.confirmLabel ?? (active.kind === "confirm" ? t("تأكيد", "Confirm") : t("حسناً", "OK"))}
                  </button>
                )}
              </div>
            </motion.section>
          </div>
        )}
      </AnimatePresence>
    </GlobalDialogContext.Provider>
  );
}
