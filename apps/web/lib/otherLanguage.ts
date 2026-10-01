import type { Language } from "@/lib/languageCookie";

/**
 * Attributes for a line in the other language (the English name under an Arabic one, or
 * the reverse). It keeps its own direction, so a long name is cut at its own end; add
 * `text-end` on block elements to line it up with the text around it.
 */
export function otherLanguage(lang: Language) {
  return lang === "ar" ? { lang: "en", dir: "ltr" as const } : { lang: "ar", dir: "rtl" as const };
}
