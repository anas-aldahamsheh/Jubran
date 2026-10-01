/** Shared by the server layout and the theme switcher (no "use client": plain values). */
export type ThemePreference = "system" | "light" | "dark";

/** Cookie the server reads so the first page already has the chosen theme (no flash). */
export const THEME_COOKIE = "jubran_theme";

export function parseThemePreference(value: string | undefined): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}
