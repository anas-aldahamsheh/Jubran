"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CircleAlert, Eye, EyeOff, LoaderCircle, LockKeyhole, LogIn, Mail, ShieldCheck, UtensilsCrossed } from "lucide-react";
import { apiFetch, ApiException } from "@/lib/api";
import { isPublicDemo, PUBLIC_DEMO_ENTRY } from "@/lib/config";
import { useLanguage } from "@/context/LanguageContext";
import { HeritageScene } from "@/components/common/HeritageScene";
import { LanguageToggle } from "@/components/common/LanguageToggle";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Logo } from "@/components/ui/Logo";

// The login itself is kept by the browser in an HttpOnly cookie set by the API;
// this page never sees (or stores) a token.
interface LoginResponse {
  user: { id: string; email: string; role: string };
}

export default function AdminLoginPage() {
  const router = useRouter();
  const { dir, t } = useLanguage();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [showPasswordHelp, setShowPasswordHelp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loading) return;
    setLoading(true);
    setError(null);

    try {
      const res = await apiFetch<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, remember_me: remember }),
      });
      if (res.user.role !== "ADMIN") {
        // Do not leave a non-admin signed in from the admin login page.
        await apiFetch("/auth/logout", { method: "POST" }).catch(() => undefined);
        setError(t("هذا الحساب ليس لديه صلاحيات الإدارة.", "This account does not have admin permissions."));
        setLoading(false);
        return;
      }
      // Signed in: the button keeps showing it is on its way until the admin page replaces this one.
      router.push("/admin/floor");
    } catch (err: unknown) {
      setError(err instanceof ApiException ? err.message : t("حدث خطأ أثناء تسجيل الدخول. يرجى التأكد من البيانات.", "An error occurred during login. Please verify your credentials."));
      setLoading(false);
    }
  };

  return (
    <main className="relative isolate flex min-h-dvh flex-col overflow-hidden bg-canvas text-ink" dir={dir}>
      <HeritageScene />

      <header className="flex items-center justify-between gap-3 px-4 pt-[max(1rem,env(safe-area-inset-top))] sm:px-8">
        <Logo className="h-9" priority />
        <div className="flex gap-2">
          <ThemeToggle />
          <LanguageToggle />
        </div>
      </header>

      {/* CSS entrances (one after another): the form shows before the page's scripts arrive. */}
      <section
        className="m-auto w-full max-w-[27rem] px-4 pb-[max(2rem,env(safe-area-inset-bottom))] pt-8"
        aria-labelledby="login-title"
      >
        {/* The public demo: visitors try the guest side; the sign-in below is for the restaurant. */}
        {isPublicDemo && (
          <Link
            href={`/t/${PUBLIC_DEMO_ENTRY}`}
            className="group animate-rise mb-4 flex items-center gap-3.5 rounded-[1.5rem] border border-brand-line bg-surface/85 p-4 shadow-float backdrop-blur-xl transition-colors hover:bg-surface sm:p-5"
          >
            <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow">
              <UtensilsCrossed className="size-6" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-display text-lg font-bold leading-tight text-ink">{t("جرّب جبران كضيف", "Try Jubran as a guest")}</span>
              <span className="mt-1 block text-sm leading-relaxed text-muted">
                {t("طاولة لك، القائمة، النادل الذكي والطلب، ومطبخ تجريبي يحضّر طلبك.", "Your own table, the menu, the smart waiter and ordering, with a pretend kitchen preparing your order.")}
              </span>
            </span>
            <ArrowRight className="size-5 shrink-0 text-brand transition-transform group-hover:translate-x-0.5 rtl:-scale-x-100 rtl:group-hover:-translate-x-0.5" aria-hidden="true" />
          </Link>
        )}

        <div className="animate-rise rounded-[2rem] border border-line bg-surface/85 p-6 shadow-float backdrop-blur-xl [--rise-delay:0.08s] sm:p-8">
          <div className="flex items-center gap-3.5">
            <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow">
              <ShieldCheck className="size-6" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-bold text-accent-ink">{t("لوحة إدارة المطعم", "Restaurant management")}</p>
              <h1 id="login-title" className="font-display text-2xl font-bold leading-tight text-ink sm:text-3xl">{t("تسجيل الدخول", "Sign in")}</h1>
            </div>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-muted">{t("أهلاً بك في جبران. سجّل دخولك لمتابعة الصالة والطلبات والقائمة.", "Welcome to Jubran. Sign in to follow the floor, orders and menu.")}</p>

          <AnimatePresence>
            {error && (
              <motion.div
                role="alert"
                initial={{ opacity: 0, height: 0, marginTop: 0 }}
                animate={{ opacity: 1, height: "auto", marginTop: 20, x: [0, -8, 8, -4, 4, 0] }}
                exit={{ opacity: 0, height: 0, marginTop: 0 }}
                transition={{ duration: 0.4 }}
                className="overflow-hidden"
              >
                <div className="flex items-start gap-2.5 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm font-medium text-danger-ink">
                  <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  {error}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            <div className="animate-rise [--rise-delay:0.18s]">
              <label htmlFor="admin-email" className="field-label">{t("البريد الإلكتروني", "Email address")}</label>
              <div className="relative" dir="ltr">
                <Mail aria-hidden="true" className="pointer-events-none absolute start-3.5 top-1/2 size-5 -translate-y-1/2 text-subtle" />
                <input id="admin-email" type="email" dir="ltr" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required className="input h-12 ps-11 text-start" placeholder="example@domain.com" />
              </div>
            </div>

            <div className="animate-rise [--rise-delay:0.28s]">
              <label htmlFor="admin-password" className="field-label">{t("كلمة المرور", "Password")}</label>
              <div className="relative" dir="ltr">
                <LockKeyhole aria-hidden="true" className="pointer-events-none absolute start-3.5 top-1/2 size-5 -translate-y-1/2 text-subtle" />
                <input id="admin-password" type={showPassword ? "text" : "password"} dir="ltr" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required className="input h-12 pe-12 ps-11 text-start" placeholder="••••••••" />
                <button type="button" onClick={() => setShowPassword((current) => !current)} className="absolute end-1.5 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-xl text-subtle transition-colors hover:bg-surface-3 hover:text-ink" aria-label={showPassword ? t("إخفاء كلمة المرور", "Hide password") : t("إظهار كلمة المرور", "Show password")} aria-pressed={showPassword}>
                  {showPassword ? <EyeOff aria-hidden="true" className="size-5" /> : <Eye aria-hidden="true" className="size-5" />}
                </button>
              </div>
            </div>

            <div className="animate-rise flex items-center justify-between gap-3 text-sm [--rise-delay:0.38s]">
              <label className="flex cursor-pointer items-center gap-2 font-medium text-ink-2">
                <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} className="size-[18px] cursor-pointer rounded accent-[var(--brand)]" />
                {t("تذكرني", "Remember me")}
              </label>
              <button type="button" onClick={() => setShowPasswordHelp((current) => !current)} className="font-semibold text-brand underline-offset-4 hover:underline">{t("نسيت كلمة المرور؟", "Forgot password?")}</button>
            </div>

            <AnimatePresence>
              {showPasswordHelp && (
                <motion.p
                  role="status"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden"
                >
                  <span className="block rounded-2xl bg-surface-3 px-4 py-3 text-sm text-ink-2">{t("لإعادة تعيين كلمة مرور الإدارة، تواصل مع مسؤول النظام.", "Contact the system administrator to reset your admin password.")}</span>
                </motion.p>
              )}
            </AnimatePresence>

            <div className="animate-rise [--rise-delay:0.48s]">
              <motion.button whileTap={{ scale: 0.98 }} type="submit" disabled={loading} className="btn btn-primary btn-lg w-full">
                {loading ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <LogIn className="size-5 rtl:-scale-x-100" aria-hidden="true" />}
                {loading ? t("جاري التحقق...", "Verifying...") : t("دخول", "Sign in")}
              </motion.button>
            </div>
          </form>
        </div>
      </section>
    </main>
  );
}
