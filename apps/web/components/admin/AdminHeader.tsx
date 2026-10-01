"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { motion } from "motion/react";
import { BookOpenText, LogOut, Map as MapIcon, QrCode, Settings, Store } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { LanguageToggle } from "@/components/common/LanguageToggle";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Logo } from "@/components/ui/Logo";
import { useLanguage } from "@/context/LanguageContext";
import { spring } from "@/lib/motion";
import { ButtonSpinner } from "@/components/ui/Feedback";

/**
 * The admin navigation: a top bar with the sections on computers, and a bottom tab bar
 * on phones and tablets (pages keep room for it with pb-24 lg:pb-*).
 */
export function AdminHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useLanguage();
  const [leaving, setLeaving] = useState(false);

  const handleLogout = async () => {
    if (leaving) return;
    setLeaving(true); // stays on until the sign-in page replaces this one
    try {
      // Ends the session on the server and clears the HttpOnly login cookie.
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // Still leave the admin area; an expired session is already signed out.
    } finally {
      router.push("/login");
    }
  };

  const navItems = [
    { name: t("الصالة المباشرة", "Live floor"), short: t("الصالة", "Floor"), icon: MapIcon, href: "/admin/floor" },
    { name: t("المطعم والقائمة", "Menu & restaurant"), short: t("القائمة", "Menu"), icon: BookOpenText, href: "/admin/menu" },
    { name: t("الطاولات و QR", "Tables & QR"), short: t("الطاولات", "Tables"), icon: QrCode, href: "/admin/tables" },
    { name: t("الإعدادات", "Settings"), short: t("الإعدادات", "Settings"), icon: Settings, href: "/admin/ai-models" },
  ];

  return (
    <>
      <motion.header layoutRoot className="sticky top-0 z-40 border-b border-line bg-canvas/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1720px] items-center justify-between gap-3 px-3 sm:px-6">
          <Link href="/admin/floor" className="flex min-w-0 items-center gap-3 rounded-lg" title={t("لوحة الإدارة", "Admin panel")}>
            <Logo className="h-8 sm:h-9" priority />
            <span className="hidden rounded-full border border-line bg-surface px-2.5 py-1 text-[0.6875rem] font-bold text-muted 2xl:inline">{t("لوحة الإدارة", "Admin")}</span>
          </Link>

          <nav className="hidden lg:block" aria-label={t("أقسام الإدارة", "Admin sections")}>
            <ul className="flex items-center gap-1 rounded-full border border-line bg-surface p-1 shadow-card">
              {navItems.map((item) => {
                const active = pathname === item.href;
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`relative isolate flex h-10 items-center gap-2 rounded-full px-4 text-sm font-semibold transition-colors ${active ? "text-on-brand" : "text-ink-2 hover:text-ink"}`}
                    >
                      {active && <motion.span layoutId="admin-top-tab" className="absolute inset-0 -z-10 rounded-full bg-brand shadow-glow" transition={spring.snappy} />}
                      <Icon className="size-[18px]" aria-hidden="true" />
                      {/* Short names until there is room for the full ones */}
                      <span className="whitespace-nowrap xl:hidden">{item.short}</span>
                      <span className="hidden whitespace-nowrap xl:inline">{item.name}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <span className="sm:hidden lg:block xl:hidden"><LanguageToggle compact /></span>
            <span className="hidden sm:block lg:hidden xl:block"><LanguageToggle /></span>
            <Link
              href="/menu"
              title={t("معاينة شاشة الزبائن", "Preview customer site")}
              aria-label={t("واجهة الزبائن", "Customer site")}
              className="hidden h-10 items-center gap-1.5 whitespace-nowrap rounded-full border border-brand-line bg-brand-soft px-3.5 text-sm font-semibold text-brand-soft-ink transition-colors hover:bg-brand hover:text-on-brand md:inline-flex"
            >
              <Store className="size-4" aria-hidden="true" />
              <span className="hidden 2xl:inline">{t("واجهة الزبائن", "Customer site")}</span>
            </Link>
            <button
              type="button"
              onClick={handleLogout}
              disabled={leaving}
              aria-busy={leaving}
              title={t("خروج", "Log out")}
              aria-label={t("خروج", "Log out")}
              className="flex h-10 items-center gap-1.5 whitespace-nowrap rounded-full border border-danger/25 bg-danger-soft px-3 text-sm font-semibold text-danger-ink transition-colors hover:bg-danger hover:text-white disabled:opacity-70"
            >
              {leaving ? <ButtonSpinner /> : <LogOut className="size-4 rtl:-scale-x-100" aria-hidden="true" />}
              <span className="hidden 2xl:inline">{t("خروج", "Log out")}</span>
            </button>
          </div>
        </div>
      </motion.header>

      {/* Phones and tablets: the sections at the thumb */}
      <motion.nav
        layoutRoot
        initial={{ y: 80, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ ...spring.sheet, delay: 0.1 }}
        aria-label={t("أقسام الإدارة", "Admin sections")}
        className="fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom))] z-40 lg:hidden"
      >
        <ul className="mx-auto flex max-w-lg items-center gap-1 rounded-[1.375rem] border border-line bg-elevated/90 p-1.5 shadow-float backdrop-blur-xl">
          {navItems.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;
            return (
              <li key={item.href} className="flex-1">
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`relative isolate flex h-14 flex-col items-center justify-center gap-1 rounded-2xl text-[0.6875rem] font-semibold transition-colors ${active ? "text-on-brand" : "text-muted active:bg-surface-3"}`}
                >
                  {active && <motion.span layoutId="admin-dock-tab" className="absolute inset-0 -z-10 rounded-2xl bg-brand shadow-glow" transition={spring.snappy} />}
                  <Icon className="size-5" aria-hidden="true" />
                  <span>{item.short}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </motion.nav>
    </>
  );
}
