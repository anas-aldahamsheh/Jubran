"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useParams, useRouter } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion, type Variants } from "motion/react";
import { ArrowRight, BellRing, MapPin, QrCode, ReceiptText, RefreshCw, UtensilsCrossed, WifiOff, X, type LucideIcon } from "lucide-react";
import { apiFetch, ApiException } from "@/lib/api";
import { HeritageScene } from "@/components/common/HeritageScene";
import { LanguageToggle } from "@/components/common/LanguageToggle";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Logo } from "@/components/ui/Logo";
import { useLanguage } from "@/context/LanguageContext";
import { EASE_OUT, fadeUp, spring, stagger } from "@/lib/motion";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { SlowNote } from "@/components/ui/Feedback";

// The visit itself is kept by the browser in an HttpOnly cookie set by the API.
interface StartSessionResponse {
  customer_id: string;
  table_id: string;
  table_number: string;
  branch_name_ar: string;
  branch_name_en?: string;
  restaurant_name_ar: string;
}

/** How long the welcome stays before the menu opens by itself: enough to read it. */
const WELCOME_MS = 15000;

const pop: Variants = {
  hidden: { opacity: 0, scale: 0.6 },
  show: { opacity: 1, scale: 1, transition: spring.pop },
};

/** A check mark that draws itself. */
function DrawnCheck() {
  return (
    <motion.svg viewBox="0 0 52 52" className="size-10" aria-hidden="true">
      <motion.circle
        cx="26" cy="26" r="24" fill="none" stroke="currentColor" strokeWidth="2.5"
        initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 0.35 }}
        transition={{ duration: 0.6, ease: EASE_OUT }}
      />
      <motion.path
        d="M15 27l7 7 15-16" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
        transition={{ duration: 0.45, ease: EASE_OUT, delay: 0.3 }}
      />
    </motion.svg>
  );
}

/** Follows its content's height smoothly: the welcome is taller than the check-in spinner. */
function SmoothHeight({ children }: { children: React.ReactNode }) {
  const inner = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | "auto">("auto");

  useEffect(() => {
    const element = inner.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.offsetHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // A little room around the content, so focus rings and shadows are never clipped.
  return (
    <motion.div initial={false} animate={{ height }} transition={spring.smooth} className="-m-3 overflow-hidden">
      <div ref={inner} className="p-3">{children}</div>
    </motion.div>
  );
}

/**
 * The table's welcome: a greeting for the time of day, where the guest is, and what they can do
 * from their table. It stays a few seconds, then opens the menu by itself (the button fills up
 * meanwhile, and opens it right away). The welcome is replaced in the history, so "back" from
 * the menu never lands here again.
 */
function TableWelcome({ session, hour }: { session: StartSessionResponse; hour: number }) {
  const { lang, t } = useLanguage();
  const router = useRouter();
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    router.prefetch("/menu");
    const timer = window.setTimeout(() => router.replace("/menu"), WELCOME_MS);
    return () => window.clearTimeout(timer);
  }, [router]);

  const greeting =
    hour >= 5 && hour < 12 ? t("صباح الخير", "Good morning")
    : hour >= 12 && hour < 17 ? t("نهارك سعيد", "Good afternoon")
    : t("مساء الخير", "Good evening");

  // What the guest can do from here, one short line each; the waiter shows its own face (no icon).
  const offers: { icon?: LucideIcon; title: string; text: string }[] = [
    { icon: UtensilsCrossed, title: t("قائمتنا بالصور والأسعار", "Menu with photos & prices"), text: t("تصفّح على راحتك واطلب من مكانك", "Browse and order from your seat") },
    { title: t("نادلك الذكي", "Your smart waiter"), text: t("اسأله عن أي طبق، ويساعدك في طلبك", "Ask anything; it helps you order") },
    { icon: ReceiptText, title: t("طلبك تحت عينك", "Your order at a glance"), text: t("تابع تحضيره لحظة بلحظة", "Follow its preparation live") },
    { icon: BellRing, title: t("خدمتك بلمسة", "Service in a tap"), text: t("موظف، محارم أو الحساب، متى احتجت", "Staff, napkins or the bill, anytime") },
  ];

  return (
    <motion.div initial="hidden" animate="show" variants={stagger(0.05, 0.07)} className="flex flex-col items-center">
      <motion.span variants={pop} className="flex size-14 items-center justify-center rounded-full bg-success-soft text-success">
        <DrawnCheck />
      </motion.span>
      <motion.p variants={fadeUp} className="mt-3 text-sm font-bold text-accent-ink">{greeting}</motion.p>
      <motion.h2 variants={fadeUp} className="mt-1 font-display text-2xl font-bold text-ink">
        {t(`أهلاً بك في طاولة ${session.table_number}`, `Welcome to table ${session.table_number}`)}
      </motion.h2>
      <motion.p variants={fadeUp} className="mt-1.5 inline-flex items-center gap-1.5 text-sm text-muted">
        <MapPin className="size-4 shrink-0 text-subtle" aria-hidden="true" />
        {lang === "en" && session.branch_name_en ? session.branch_name_en : session.branch_name_ar}
      </motion.p>

      {/* Two whole phrases, one per line (never broken mid-phrase on a phone) */}
      <motion.p variants={fadeUp} className="mt-5 text-sm leading-relaxed text-ink-2">
        <span className="block">{t("يسعدنا وجودك معنا،", "We're glad you're here.")}</span>
        <span className="block">{t("وكل ما تحتاجه صار على طاولتك:", "Everything you need is at your table:")}</span>
      </motion.p>
      <motion.ul variants={stagger(0, 0.06)} className="mt-3 w-full divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface-2/80 text-start">
        {offers.map((offer) => (
          <motion.li key={offer.title} variants={fadeUp} className="flex items-center gap-3 px-3.5 py-2">
            {offer.icon ? (
              <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-soft-ink">
                <offer.icon className="size-5" aria-hidden="true" />
              </span>
            ) : (
              <span className="relative shrink-0">
                <Image
                  src="/brand/jubran-emblem.webp"
                  alt=""
                  width={80}
                  height={80}
                  className="size-10 rounded-full bg-gradient-to-br from-[#41579e] to-[#151e40] object-cover ring-2 ring-brand-line"
                />
                <span className="absolute -bottom-0.5 -end-0.5 flex size-3.5 items-center justify-center rounded-full bg-surface-2" aria-hidden="true">
                  <span className="size-2 rounded-full bg-success" />
                </span>
              </span>
            )}
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-ink">{offer.title}</span>
              <span className="block text-xs leading-relaxed text-muted">{offer.text}</span>
            </span>
          </motion.li>
        ))}
      </motion.ul>

      <motion.div variants={fadeUp} className="mt-5 w-full">
        <button type="button" onClick={() => router.replace("/menu")} className="btn btn-primary btn-lg w-full overflow-hidden">
          {/* Fills up while the welcome waits, like a gentle "opening the menu…" */}
          {!reduceMotion && (
            <motion.span
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 origin-left bg-white/15 rtl:origin-right"
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ duration: WELCOME_MS / 1000, ease: "linear" }}
            />
          )}
          <span className="relative inline-flex items-center gap-2">
            {t("عرض قائمة الطعام والطلب", "View menu & order")}
            <ArrowRight className="size-5 rtl:-scale-x-100" aria-hidden="true" />
          </span>
        </button>
      </motion.div>
    </motion.div>
  );
}

export default function QrEntryPage() {
  const { dir, t } = useLanguage();
  const params = useParams();
  const token = params?.token as string;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionData, setSessionData] = useState<StartSessionResponse | null>(null);
  const [welcomeHour, setWelcomeHour] = useState(12);
  // The connection failed (not a wrong code): failures in a row, retried by itself.
  const [offlineFailures, setOfflineFailures] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const retry = () => setAttempt((current) => current + 1);
  useAutoRetry(offlineFailures, retry);

  useEffect(() => {
    async function resolveQrToken() {
      if (!token) {
        setError(t("تعذر التعرف على الطاولة. يرجى مسح رمز الطاولة مرة أخرى.", "Could not identify the table. Please scan the table QR code again."));
        setLoading(false);
        return;
      }
      try {
        setLoading(true);
        setError(null);
        const data = await apiFetch<StartSessionResponse>("/table-sessions/start", {
          method: "POST",
          body: JSON.stringify({ qr_token: token }),
        });
        setWelcomeHour(new Date().getHours());
        setOfflineFailures(0);
        setSessionData(data);
      } catch (err: unknown) {
        if (err instanceof ApiException && err.code === "NETWORK_ERROR") {
          setOfflineFailures((count) => count + 1);
        } else if (err instanceof ApiException) {
          setOfflineFailures(0);
          setError(err.message);
        } else {
          setError(t("تعذر التعرف على الطاولة. يرجى مسح رمز الطاولة مرة أخرى.", "Could not identify the table. Please scan the table QR code again."));
        }
      } finally {
        setLoading(false);
      }
    }

    void Promise.resolve().then(resolveQrToken);
    // The table is resolved once per QR code (and again only when retrying after a lost
    // connection); switching the language must not scan it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, attempt]);
  const offline = !loading && !error && !sessionData && offlineFailures > 0;

  return (
    <main className="relative isolate flex min-h-dvh flex-col overflow-hidden bg-canvas text-ink" dir={dir}>
      <HeritageScene photo="rooftop" />

      <header className="flex justify-end gap-2 px-4 pt-[max(1rem,env(safe-area-inset-top))] sm:px-8">
        <ThemeToggle />
        <LanguageToggle compact />
      </header>

      <section className="m-auto w-full max-w-md px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-4">
        {/* A CSS entrance: the card shows from the first paint, even before the scripts arrive. */}
        <div className="animate-rise rounded-[2rem] border border-line bg-surface/90 p-6 text-center shadow-float backdrop-blur-xl [--rise-from:24px] [--rise-scale:0.97] sm:p-8">
          <Logo className="mx-auto h-12 sm:h-14" priority />
          <p className="mt-2 text-sm text-muted">{t("تراث المشرق بروح جديدة فوق عمّان", "Levantine heritage, reimagined above Amman")}</p>

          <div className="mt-6">
            <SmoothHeight>
              <div className="flex min-h-56 flex-col justify-center">
                <AnimatePresence mode="wait" initial={false}>
                  {loading && (
                    <motion.div key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, scale: 0.96 }} transition={{ duration: 0.25 }} className="flex flex-col items-center">
                      <div className="relative flex size-24 items-center justify-center">
                        <motion.span
                          className="absolute inset-0 rounded-full border-[3px] border-brand-soft border-t-brand"
                          animate={{ rotate: 360 }}
                          transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                          aria-hidden="true"
                        />
                        <motion.span animate={{ scale: [1, 1.08, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}>
                          <QrCode className="size-10 text-brand" strokeWidth={1.5} aria-hidden="true" />
                        </motion.span>
                      </div>
                      <p className="mt-5 font-semibold text-ink" role="status">{t("جاري التحقق من الطاولة وبدء جلستك...", "Checking your table and starting your visit...")}</p>
                      <SlowNote after={5} className="mt-3" />
                    </motion.div>
                  )}

                  {offline && (
                    <motion.div key="offline" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.3, ease: EASE_OUT }} className="flex flex-col items-center" role="alert">
                      <span className="flex size-16 items-center justify-center rounded-full bg-warning-soft text-warning-ink">
                        <WifiOff className="size-8" aria-hidden="true" />
                      </span>
                      <h2 className="mt-4 font-display text-xl font-bold text-ink">{t("الاتصال ضعيف", "The connection is weak")}</h2>
                      <p className="mt-2 text-sm leading-relaxed text-muted">{t("ما قدرنا نوصل للمطعم هلأ. رح نعيد المحاولة لحالنا أول ما يرجع النت.", "We couldn't reach the restaurant just now. We'll try again by ourselves as soon as the connection is back.")}</p>
                      <button type="button" onClick={retry} className="btn btn-primary mt-5">
                        <RefreshCw className="size-[18px]" aria-hidden="true" />
                        {t("إعادة المحاولة", "Try again")}
                      </button>
                    </motion.div>
                  )}

                  {error && (
                    <motion.div
                      key="error"
                      initial={{ opacity: 0, x: 0 }}
                      animate={{ opacity: 1, x: [0, -10, 10, -6, 6, 0] }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.5 }}
                      className="flex flex-col items-center"
                      role="alert"
                    >
                      <span className="flex size-16 items-center justify-center rounded-full bg-danger-soft text-danger">
                        <X className="size-8" aria-hidden="true" />
                      </span>
                      <h2 className="mt-4 font-display text-xl font-bold text-danger-ink">{t("رمز الطاولة غير صالح", "Invalid table code")}</h2>
                      <p className="mt-2 text-sm leading-relaxed text-muted">{error}</p>
                      <p className="mt-5 border-t border-line pt-4 text-xs leading-relaxed text-subtle">
                        {t("يرجى مسح رمز QR الموجود على طاولتك أو إبلاغ أحد موظفي المطعم للمساعدة.", "Please scan the QR code on your table or ask a staff member for help.")}
                      </p>
                    </motion.div>
                  )}

                  {sessionData && !loading && !error && (
                    <TableWelcome key="welcome" session={sessionData} hour={welcomeHour} />
                  )}
                </AnimatePresence>
              </div>
            </SmoothHeight>
          </div>
        </div>
      </section>
    </main>
  );
}
