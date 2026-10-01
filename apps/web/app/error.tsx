"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import { House, RefreshCw, TriangleAlert } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { ButtonSpinner } from "@/components/ui/Feedback";
import { EASE_OUT } from "@/lib/motion";

/**
 * A page that failed to show (an unexpected error while drawing it): a calm card instead of a
 * blank screen, with "try again" (draws the page again) and a way home.
 */
export default function PageError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  const { dir, t } = useLanguage();
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex flex-1 flex-col items-center justify-center p-6 text-center" dir={dir}>
      <motion.div
        initial={{ opacity: 0, y: 14, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: EASE_OUT }}
        className="w-full max-w-md rounded-[2rem] border border-line bg-surface p-7 shadow-float"
        role="alert"
      >
        <span className="mx-auto flex size-16 items-center justify-center rounded-3xl bg-warning-soft text-warning-ink">
          <TriangleAlert className="size-8" strokeWidth={1.75} aria-hidden="true" />
        </span>
        <h1 className="mt-4 font-display text-xl font-bold text-ink">{t("صار خلل بسيط بعرض الصفحة", "Something went wrong showing this page")}</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          {t("طلباتك وسلتك محفوظة. جرّب مرة ثانية، وإذا ضل الخلل ارجع للقائمة.", "Your orders and basket are safe. Try again, and if it keeps happening go back to the menu.")}
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <button
            type="button"
            onClick={() => {
              setRetrying(true);
              retry();
              window.setTimeout(() => setRetrying(false), 1500);
            }}
            disabled={retrying}
            className="btn btn-primary"
          >
            {retrying ? <ButtonSpinner className="size-[18px]" /> : <RefreshCw className="size-[18px]" aria-hidden="true" />}
            {t("حاول مرة ثانية", "Try again")}
          </button>
          <Link href="/menu" className="btn btn-secondary">
            <House className="size-[18px]" aria-hidden="true" />
            {t("القائمة", "The menu")}
          </Link>
        </div>
      </motion.div>
    </main>
  );
}
