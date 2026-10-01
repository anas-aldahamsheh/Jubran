/** Shared by the server layout and the language switcher (no "use client": plain values). */
export type Language = "ar" | "en";

/** Cookie the server reads to render the first page in the right language (no flash). */
export const LANGUAGE_COOKIE = "jubran_lang";
