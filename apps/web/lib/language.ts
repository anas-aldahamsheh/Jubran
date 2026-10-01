/** Locale for speech recognition, speech synthesis and times, from the language the guest picked. */
export function speechLocale(lang: string): string {
  return lang === "en" ? "en-US" : "ar-JO";
}
