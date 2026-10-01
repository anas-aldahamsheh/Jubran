"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { BookOpenText, QrCode } from "lucide-react";
import { spring } from "@/lib/motion";
import { getCustomerSessionContext } from "@/lib/api";
import { useLanguage } from "@/context/LanguageContext";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { VISIT_ENDED_EVENT, announceVisitEnded, announceVisitSignal } from "@/lib/visitStatus";

/** Pages a guest uses during a table visit. */
const GUEST_PAGES = ["/menu", "/orders", "/assistant"];

function isGuestPage(pathname: string): boolean {
  return GUEST_PAGES.some((page) => pathname === page || pathname.startsWith(`${page}/`));
}

/**
 * Keeps one live connection to the guest's table (pages listen with useVisitSignals)
 * and, when the visit ends, tells the guest instead of letting requests fail.
 */
export function VisitStatus() {
  const pathname = usePathname();
  const { t, dir } = useLanguage();
  const [visitId, setVisitId] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const buttonRef = useRef<HTMLAnchorElement>(null);
  const guestPage = isGuestPage(pathname);

  useEffect(() => {
    if (!guestPage) return;
    let ignore = false;
    getCustomerSessionContext()
      .then((context) => {
        if (ignore || !context) return;
        // A visit is running (again, after a new QR scan).
        setVisitId(context.table_session_id);
        setEnded(false);
        setDismissed(false);
      })
      .catch(() => {
        // Server unreachable: pages show their own connection errors.
      });
    return () => {
      ignore = true;
    };
  }, [guestPage, pathname]);

  useEffect(() => {
    // Once dismissed, the notice stays away until a new visit starts and ends.
    const onEnded = () => setEnded(true);
    window.addEventListener(VISIT_ENDED_EVENT, onEnded);
    return () => window.removeEventListener(VISIT_ENDED_EVENT, onEnded);
  }, []);

  useLiveSocket(
    guestPage && visitId && !ended ? `/ws/customer/${encodeURIComponent(visitId)}` : null,
    (message) => {
      announceVisitSignal(message);
      if (message.type === "visit.closed") announceVisitEnded();
    },
    () => announceVisitSignal({ type: "reconnected" }),
  );

  const visible = guestPage && ended && !dismissed;
  useEffect(() => {
    if (visible) buttonRef.current?.focus();
  }, [visible]);

  return (
    <AnimatePresence>
      {visible && (
        <div key="visit-ended" className="fixed inset-0 z-[180] flex items-end justify-center p-3 sm:items-center sm:p-4" dir={dir}>
          <motion.div
            aria-hidden="true"
            className="absolute inset-0 bg-[var(--overlay)] backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.section
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="visit-ended-title"
            aria-describedby="visit-ended-message"
            initial={{ opacity: 0, y: 30, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.97 }}
            transition={spring.sheet}
            className="relative w-full max-w-sm rounded-[1.75rem] border border-line bg-elevated p-6 pb-[max(1.5rem,env(safe-area-inset-bottom))] text-center shadow-float sm:pb-6"
          >
            <motion.span
              initial={{ scale: 0.4, rotate: -30 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ ...spring.pop, delay: 0.1 }}
              className="mx-auto mb-4 flex size-16 items-center justify-center rounded-3xl bg-brand-soft text-brand"
              aria-hidden="true"
            >
              <QrCode className="size-8" strokeWidth={1.5} />
            </motion.span>
            <h2 id="visit-ended-title" className="font-display text-2xl font-bold text-ink">
              {t("انتهت زيارتك", "Your visit has ended")}
            </h2>
            <p id="visit-ended-message" className="mt-2 text-sm leading-7 text-muted">
              {t(
                "شكراً لزيارتكم! جلسة الطاولة انتهت. لطلب جديد امسح رمز QR الموجود على الطاولة من جديد.",
                "Thank you for visiting! This table session has ended. To order again, scan the QR code on the table.",
              )}
            </p>
            <div className="mt-6 flex flex-col gap-2">
              <Link ref={buttonRef} href="/menu" onClick={() => setDismissed(true)} className="btn btn-primary">
                <BookOpenText className="size-[18px]" aria-hidden="true" />
                {t("تصفّح المنيو", "Browse the menu")}
              </Link>
            </div>
          </motion.section>
        </div>
      )}
    </AnimatePresence>
  );
}
