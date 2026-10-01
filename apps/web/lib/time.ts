/** The restaurant's clock: times are shown in Amman time, whatever the device is set to. */
export const RESTAURANT_TIME_ZONE = "Asia/Amman";

/** Format a moment (ISO text, number or Date) in the restaurant's time zone. */
export function restaurantTime(value: string | number | Date, locale: string,
                               options: Intl.DateTimeFormatOptions = {}): string {
  const moment = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(moment.getTime())) return "";
  return moment.toLocaleString(locale, { timeZone: RESTAURANT_TIME_ZONE, ...options });
}
