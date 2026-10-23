/**
 * How a dish is counted: by the plate, the glass, the box... The server sends every unit with
 * its Arabic and English label (common units are translated, others show as the admin wrote them).
 */

/** A basket or order line's unit (missing on lines from before units existed). */
export interface LineUnit {
  unit?: string | null;
  unit_display_ar?: string | null;
  unit_display_en?: string | null;
}

/** A dish's units: one, or several when it is served more than one way (a plate or a box). */
export interface DishUnits {
  units?: string[];
  units_display_ar?: string[];
  units_display_en?: string[];
}

/** The units the admin can pick with one tap; any other unit can be typed. */
export const SUGGESTED_UNITS = ["صحن", "كاسة", "علبة", "قنينة", "حبة", "ساندويشة", "فنجان"];
/** More common units, offered while typing. */
const MORE_UNITS = ["كوب", "قطعة", "رغيف", "إبريق", "زبدية", "سيخ", "وجبة", "كيلو"];
/** Same limits as the server (MAX_UNITS, MAX_UNIT_LENGTH). */
export const MAX_UNITS = 6;
export const MAX_UNIT_LENGTH = 40;

/** A line's unit in the screen's language. */
export function unitLabel(line: LineUnit, lang: "ar" | "en"): string | null {
  return (lang === "ar" ? line.unit_display_ar : line.unit_display_en) || line.unit || null;
}

/** A dish's units, each with its label in the screen's language. */
export function dishUnits(dish: DishUnits, lang: "ar" | "en"): Array<{ value: string; label: string }> {
  const labels = lang === "ar" ? dish.units_display_ar : dish.units_display_en;
  return (dish.units || []).map((value, index) => ({ value, label: labels?.[index] || value }));
}

/** Units to suggest for what the admin is typing, without the ones already chosen. */
export function unitSuggestions(typed: string, chosen: string[]): string[] {
  const text = typed.trim();
  const pool = text ? [...SUGGESTED_UNITS, ...MORE_UNITS].filter((unit) => unit.includes(text)) : SUGGESTED_UNITS;
  return pool.filter((unit) => !chosen.includes(unit));
}

/** The admin's units: trimmed, one space between words, no repeats, within the limits. */
export function cleanUnits(values: string[]): string[] {
  const cleaned: string[] = [];
  for (const value of values) {
    const unit = value.replace(/\s+/g, " ").trim().slice(0, MAX_UNIT_LENGTH);
    if (unit && !cleaned.includes(unit) && cleaned.length < MAX_UNITS) cleaned.push(unit);
  }
  return cleaned;
}
