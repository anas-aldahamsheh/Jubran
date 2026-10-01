/**
 * Prices typed in dinars with up to 3 decimals (fils). "3.4abc" or "3.4567" are refused,
 * never cut or rounded, and the conversion uses whole numbers only (no floating point).
 */
const PRICE_PATTERN = /^\d{1,4}(?:\.\d{1,3})?$/;

/** Fils as the restaurant shows them (like the server): "3.45 د.أ" / "3.45 JOD", no trailing zeros. */
export function formatFils(fils: number, lang: "ar" | "en"): string {
  const whole = Math.trunc(Math.abs(fils) / 1000);
  const fraction = String(Math.abs(fils) % 1000).padStart(3, "0").replace(/0+$/, "");
  const amount = `${fils < 0 ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
  return lang === "ar" ? `${amount} د.أ` : `${amount} JOD`;
}

export function priceToFils(value: string): number | null {
  const text = value.trim();
  if (!PRICE_PATTERN.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const fils = Number(whole) * 1000 + Number(fraction.padEnd(3, "0"));
  return fils > 0 ? fils : null;
}
