"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { BookOpenText, UtensilsCrossed } from "lucide-react";
import { LanguageToggle } from "@/components/common/LanguageToggle";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Logo } from "@/components/ui/Logo";
import { useLanguage } from "@/context/LanguageContext";
import { EASE_OUT, fadeUp, stagger } from "@/lib/motion";

export default function NotFound() {
  const { dir, t } = useLanguage();

  return (
    <main className="relative isolate flex min-h-dvh flex-col bg-canvas px-4 py-5" dir={dir}>
      <span className="heritage-pattern pointer-events-none absolute inset-0 -z-10 text-ink opacity-[0.035]" aria-hidden="true" />
      <div className="mx-auto flex w-full max-w-4xl items-center justify-between">
        <Link href="/menu" className="rounded-lg" aria-label={t("قائمة جبران", "Jubran menu")}><Logo className="h-8" /></Link>
        <div className="flex gap-2">
          <ThemeToggle />
          <LanguageToggle compact />
        </div>
      </div>
      <motion.div initial="hidden" animate="show" variants={stagger(0.05, 0.1)} className="m-auto flex max-w-md flex-col items-center py-16 text-center">
        <motion.div variants={fadeUp} className="relative">
          <span className="font-display text-[7rem] font-bold leading-none text-brand/15 sm:text-[9rem]">404</span>
          <motion.span
            className="absolute inset-0 flex items-center justify-center"
            animate={{ rotate: [0, -8, 8, 0], y: [0, -6, 0] }}
            transition={{ duration: 3.6, repeat: Infinity, ease: "easeInOut" }}
          >
            <span className="flex size-20 items-center justify-center rounded-3xl bg-surface text-brand shadow-lift">
              <UtensilsCrossed className="size-9" strokeWidth={1.75} aria-hidden="true" />
            </span>
          </motion.span>
        </motion.div>
        <motion.h1 variants={fadeUp} className="mt-4 font-display text-2xl font-bold text-ink sm:text-3xl">
          {t("الصفحة غير موجودة", "Page not found")}
        </motion.h1>
        <motion.p variants={fadeUp} className="mt-2 text-muted">
          {t("تحقق من الرابط، أو تصفّح قائمة الطعام.", "Check the link, or browse the menu.")}
        </motion.p>
        <motion.div variants={fadeUp} transition={{ ease: EASE_OUT }}>
          <Link href="/menu" className="btn btn-primary mt-7">
            <BookOpenText className="size-[18px]" aria-hidden="true" />
            {t("قائمة الطعام", "Browse the menu")}
          </Link>
        </motion.div>
      </motion.div>
    </main>
  );
}
