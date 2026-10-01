"use client";

import Link from "next/link";
import { AnimatePresence, motion, useScroll, useTransform } from "motion/react";
import { BookOpenText, ReceiptText } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { LanguageToggle } from "@/components/common/LanguageToggle";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Logo } from "@/components/ui/Logo";
import { formatTableNumber, useTableNumber } from "@/lib/useTableNumber";
import { spring } from "@/lib/motion";

type GuestSection = "menu" | "orders";

/** The basket count on a tab: pops when it changes. */
function CountBadge({ count, className = "" }: { count: number; className?: string }) {
  const { t } = useLanguage();
  return (
    <AnimatePresence initial={false}>
      {count > 0 && (
        <motion.span
          key={count}
          initial={{ scale: 0.4, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.4, opacity: 0 }}
          transition={spring.pop}
          className={`flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1.5 text-[0.6875rem] font-bold leading-none text-white tabular-nums ring-2 ring-surface ${className}`}
          aria-label={t(`${count} في السلة`, `${count} in basket`)}
        >
          {count}
        </motion.span>
      )}
    </AnimatePresence>
  );
}

function TableChip({ className = "" }: { className?: string }) {
  const { t } = useLanguage();
  const tableNumber = useTableNumber();
  if (!tableNumber) return null;
  return (
    <span
      className={`inline-flex h-10 items-center gap-2 rounded-full border border-brand-line bg-brand-soft ps-2 pe-3.5 text-sm font-bold text-brand-soft-ink ${className}`}
      title={t("رقم طاولتك", "Your table")}
    >
      <span className="relative flex size-2" aria-hidden="true">
        <span className="absolute inset-0 animate-ping rounded-full bg-success opacity-50" />
        <span className="relative size-2 rounded-full bg-success" />
      </span>
      <span className="sr-only">{t("طاولتك", "Your table")}</span>
      <span className="tabular-nums" dir="ltr">{formatTableNumber(tableNumber)}</span>
    </span>
  );
}

/**
 * The guest's top bar on every visit page. Phones: logo, table and settings (the
 * sections are in the bottom dock). Tablets and computers: the sections are here.
 */
export function GuestTopBar({ active, itemCount }: { active: GuestSection; itemCount: number }) {
  const { t } = useLanguage();
  // The bar's glass fades in with the scroll, straight on motion values: no re-render
  // while scrolling (re-renders made the tab highlight shake inside the sticky bar).
  const { scrollY } = useScroll();
  const glass = useTransform(scrollY, [0, 28], [0, 1]);
  const veil = useTransform(scrollY, [0, 28], [1, 0]);

  const tabs: Array<{ id: GuestSection; href: string; label: string; icon: typeof BookOpenText }> = [
    { id: "menu", href: "/menu", label: t("القائمة", "Menu"), icon: BookOpenText },
    { id: "orders", href: "/orders", label: t("طلباتي", "My orders"), icon: ReceiptText },
  ];

  return (
    <motion.header layoutRoot className="sticky top-0 z-40">
      <motion.div style={{ opacity: veil }} className="pointer-events-none absolute inset-x-0 top-0 -bottom-8 -z-10 bg-gradient-to-b from-canvas via-canvas/75 to-canvas/0" aria-hidden="true" />
      <motion.div style={{ opacity: glass }} className="pointer-events-none absolute inset-0 -z-10 border-b border-line bg-canvas/80 shadow-hairline backdrop-blur-xl" aria-hidden="true" />
      <div className="relative mx-auto flex h-16 max-w-[1600px] items-center justify-between gap-3 px-4 sm:px-6 md:h-[4.5rem] lg:px-8">
        <Link href="/menu" className="rounded-lg" aria-label={t("قائمة جبران", "Jubran menu")}>
          <Logo className="h-8 md:h-9" priority />
        </Link>

        <nav aria-label={t("التنقل الرئيسي", "Main navigation")} className="hidden md:block">
          <ul className="flex items-center gap-1 rounded-full border border-line bg-surface/80 p-1 shadow-card backdrop-blur">
            {tabs.map((tab) => {
              const current = tab.id === active;
              const Icon = tab.icon;
              return (
                <li key={tab.id}>
                  <Link
                    href={tab.href}
                    aria-current={current ? "page" : undefined}
                    className={`relative isolate flex h-10 items-center gap-2 rounded-full px-5 text-sm font-semibold transition-colors ${current ? "text-on-brand" : "text-ink-2 hover:text-ink"}`}
                  >
                    {current && (
                      <motion.span layoutId="guest-top-tab" className="absolute inset-0 -z-10 rounded-full bg-brand shadow-glow" transition={spring.snappy} />
                    )}
                    <Icon className="size-[18px]" aria-hidden="true" />
                    <span>{tab.label}</span>
                    {tab.id === "orders" && <CountBadge count={itemCount} />}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex items-center gap-2">
          <TableChip />
          <ThemeToggle />
          <span className="sm:hidden"><LanguageToggle compact /></span>
          <span className="hidden sm:block"><LanguageToggle /></span>
        </div>
      </div>
    </motion.header>
  );
}

/**
 * Phones only: a floating dock with the two sections. It leaves the corner free for
 * the assistant's bubble, on the same line.
 */
export function GuestBottomNav({ active, itemCount }: { active: GuestSection; itemCount: number }) {
  const { t } = useLanguage();
  const tabs: Array<{ id: GuestSection; href: string; label: string; icon: typeof BookOpenText }> = [
    { id: "menu", href: "/menu", label: t("القائمة", "Menu"), icon: BookOpenText },
    { id: "orders", href: "/orders", label: t("طلباتي", "My orders"), icon: ReceiptText },
  ];

  return (
    <motion.nav
      layoutRoot
      aria-label={t("التنقل الرئيسي", "Main navigation")}
      // A CSS entrance: the dock is there from the first paint, even before the scripts arrive.
      className="animate-rise fixed bottom-[max(0.75rem,env(safe-area-inset-bottom))] start-3 z-40 [--rise-delay:0.15s] [--rise-from:90px] md:hidden"
    >
      <ul className="flex items-center gap-1 rounded-[1.375rem] border border-line bg-elevated/90 p-1.5 shadow-float backdrop-blur-xl">
        {tabs.map((tab) => {
          const current = tab.id === active;
          const Icon = tab.icon;
          return (
            <li key={tab.id}>
              <Link
                href={tab.href}
                aria-current={current ? "page" : undefined}
                className={`relative isolate flex h-[3.25rem] min-w-[5.75rem] items-center justify-center gap-2 rounded-2xl px-4 text-sm font-semibold transition-colors ${current ? "text-on-brand" : "text-ink-2 active:bg-surface-3"}`}
              >
                {current && (
                  <motion.span layoutId="guest-dock-tab" className="absolute inset-0 -z-10 rounded-2xl bg-brand shadow-glow" transition={spring.snappy} />
                )}
                <span className="relative">
                  <Icon className="size-5" aria-hidden="true" />
                  {tab.id === "orders" && <CountBadge count={itemCount} className="absolute -end-3 -top-2.5" />}
                </span>
                <span>{tab.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </motion.nav>
  );
}
