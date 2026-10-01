"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiException, getCurrentAccount } from "@/lib/api";
import { AnimatePresence, motion } from "motion/react";
import { RefreshCw, WifiOff } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { Logo } from "@/components/ui/Logo";
import { SlowNote } from "@/components/ui/Feedback";
import { EASE_OUT } from "@/lib/motion";

type GateState = "checking" | "allowed" | "offline";

/**
 * Admin pages render only after the API confirms (from the HttpOnly login
 * cookie) that an administrator is signed in; anyone else goes to /login.
 * The check shows the restaurant's mark with a running line, and melts into the page.
 */
export function AdminGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { dir, t } = useLanguage();
  const [state, setState] = useState<GateState>("checking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let ignore = false;
    getCurrentAccount()
      .then((account) => {
        if (ignore) return;
        if (account.authenticated && account.user?.role === "ADMIN") setState("allowed");
        else router.replace("/login");
      })
      .catch((error: unknown) => {
        if (ignore) return;
        if (error instanceof ApiException && (error.status === 401 || error.status === 403)) router.replace("/login");
        else setState("offline");
      });
    return () => {
      ignore = true;
    };
  }, [router, attempt]);

  // Offline: try again by itself as soon as the connection is back.
  useEffect(() => {
    if (state !== "offline") return;
    const retry = () => {
      setState("checking");
      setAttempt((current) => current + 1);
    };
    const timer = window.setTimeout(retry, 5000);
    window.addEventListener("online", retry);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("online", retry);
    };
  }, [state]);

  return (
    // The check melts away over the page as it appears (no wait between them).
    <AnimatePresence mode="popLayout" initial={false}>
      {state === "allowed" ? (
        <motion.div key="allowed" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35, ease: EASE_OUT }} className="flex flex-1 flex-col">
          {children}
        </motion.div>
      ) : (
        <motion.main
          key="gate"
          exit={{ opacity: 0, transition: { duration: 0.2 } }}
          className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-canvas p-6 text-center text-ink"
          dir={dir}
        >
          {/* CSS entrances: visible from the first paint, before the page's scripts arrive. */}
          <div className="animate-rise [--rise-duration:0.5s] [--rise-from:0px] [--rise-scale:0.94]">
            <Logo className="h-12" priority />
          </div>
          {state === "checking" ? (
            <div className="animate-rise flex flex-col items-center gap-4 [--rise-delay:0.2s] [--rise-from:6px]">
              <div className="h-1 w-40 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
                <div className="h-full w-1/3 animate-route rounded-full bg-brand rtl:[animation-direction:reverse]" />
              </div>
              <p className="text-sm font-medium text-muted" role="status">{t("جاري التحقق من تسجيل الدخول...", "Checking your sign-in...")}</p>
              <SlowNote after={5} />
            </div>
          ) : (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="flex max-w-sm flex-col items-center gap-4">
              <span className="flex size-14 items-center justify-center rounded-2xl bg-danger-soft text-danger"><WifiOff className="size-7" aria-hidden="true" /></span>
              <p className="text-sm font-medium text-danger-ink" role="alert">
                {t("تعذر الاتصال بالخادم. رح نعيد المحاولة لحالنا، أو اضغط الزر.", "Could not reach the server. We'll keep trying, or tap the button.")}
              </p>
              <button
                type="button"
                onClick={() => {
                  setState("checking");
                  setAttempt((current) => current + 1);
                }}
                className="btn btn-primary"
              >
                <RefreshCw className="size-[18px]" aria-hidden="true" />
                {t("إعادة المحاولة", "Try again")}
              </button>
            </motion.div>
          )}
        </motion.main>
      )}
    </AnimatePresence>
  );
}
