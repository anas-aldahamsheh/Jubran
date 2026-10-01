"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { LANGUAGE_COOKIE, type Language } from "@/lib/languageCookie";

export type { Language };
const LEGACY_STORAGE_KEY = "jubran_lang";

interface LanguageContextType {
  lang: Language;
  dir: "rtl" | "ltr";
  toggleLanguage: () => void;
  setLanguage: (lang: Language) => void;
  t: (ar: string, en: string) => string;
}

const LanguageContext = createContext<LanguageContextType>({
  lang: "ar",
  dir: "rtl",
  toggleLanguage: () => {},
  setLanguage: () => {},
  t: (ar) => ar,
});

function rememberLanguage(lang: Language) {
  document.cookie = `${LANGUAGE_COOKIE}=${lang}; path=/; max-age=31536000; samesite=lax`;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  document.documentElement.lang = lang;
}

export function LanguageProvider({ initialLang = "ar", children }: { initialLang?: Language; children: React.ReactNode }) {
  const [lang, setLangState] = useState<Language>(initialLang);

  // Browsers that chose a language before it was kept in a cookie: carry it over once.
  useEffect(() => {
    if (document.cookie.split("; ").some((part) => part.startsWith(`${LANGUAGE_COOKIE}=`))) return;
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(LEGACY_STORAGE_KEY);
    } catch {
      return;
    }
    if (saved !== "ar" && saved !== "en") return;
    rememberLanguage(saved);
    if (saved !== initialLang) {
      const frame = window.requestAnimationFrame(() => setLangState(saved as Language));
      return () => window.cancelAnimationFrame(frame);
    }
  }, [initialLang]);

  const setLanguage = useCallback((newLang: Language) => {
    setLangState(newLang);
    rememberLanguage(newLang);
  }, []);

  // Stable between renders (they change only with the language), so pages can list
  // them in effect dependencies without re-running work on every render.
  const value = useMemo<LanguageContextType>(() => ({
    lang,
    dir: lang === "ar" ? "rtl" : "ltr",
    setLanguage,
    toggleLanguage: () => setLanguage(lang === "ar" ? "en" : "ar"),
    t: (ar: string, en: string) => (lang === "ar" ? ar : en),
  }), [lang, setLanguage]);

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  return useContext(LanguageContext);
}
