"use client";

import { useLanguage } from "@/context/LanguageContext";

/** Arabic ↔ English. Shows the language it switches to, in that language (short on phones). */
export function LanguageToggle({ className = "", compact = false }: { className?: string; compact?: boolean }) {
  const { lang, toggleLanguage } = useLanguage();
  const label = lang === "ar" ? "Switch to English" : "التحويل إلى العربية";

  return (
    <button
      type="button"
      onClick={toggleLanguage}
      className={`inline-flex h-10 items-center justify-center rounded-full border border-line bg-surface px-4 text-[0.8125rem] font-semibold text-ink-2 shadow-hairline transition-colors hover:bg-surface-3 hover:text-ink ${compact ? "w-10 px-0" : ""} ${className}`}
      title={label}
      aria-label={label}
    >
      <span className="leading-none" lang={lang === "ar" ? "en" : "ar"}>
        {lang === "ar" ? (compact ? "EN" : "English") : (compact ? "ع" : "العربية")}
      </span>
    </button>
  );
}
