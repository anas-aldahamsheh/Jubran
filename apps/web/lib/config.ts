/** Public runtime configuration for the web app. */

/**
 * Demo tools (such as opening a table as a customer from the admin page).
 * On by default under `next dev`, off by default in production builds
 * (`next build` / `next start`). `NEXT_PUBLIC_DEMO_MODE=true|false` overrides both.
 */
const demoModeSetting = process.env.NEXT_PUBLIC_DEMO_MODE;
export const isDemoMode = demoModeSetting
  ? demoModeSetting === "true"
  : process.env.NODE_ENV === "development";

/**
 * A copy anyone on the internet can try (NEXT_PUBLIC_PUBLIC_DEMO=true at build time, with
 * PUBLIC_DEMO=true on the API): the sign-in page offers "Try as a guest", which opens
 * /t/demo and the API seats the visitor at a free table of their own.
 */
export const isPublicDemo = process.env.NEXT_PUBLIC_PUBLIC_DEMO === "true";
export const PUBLIC_DEMO_ENTRY = "demo";

/** Base address printed inside table QR codes. */
export function getPublicSiteUrl(): string {
  const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  return typeof window === "undefined" ? "" : window.location.origin;
}

/** Full customer entry link encoded in a table's QR code. */
export function getTableEntryUrl(token: string): string {
  return `${getPublicSiteUrl()}/t/${encodeURIComponent(token)}`;
}

/** True when QR links would point at this computer only and could not be opened from a phone. */
export function isLocalOnlySiteUrl(): boolean {
  try {
    const { hostname } = new URL(getPublicSiteUrl());
    return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
  } catch {
    return false;
  }
}
