import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";
const configuredApi = process.env.NEXT_PUBLIC_API_URL?.trim();

/** Where the browser may send API requests, sockets and load product images from. */
function apiSources(): string[] {
  if (!configuredApi) {
    // Local/LAN testing: the web app talks to port 8001 on whatever host it was opened from
    // (same protocol as the page, see getApiBaseUrl).
    return ["http://*:8001", "ws://*:8001", "https://*:8001", "wss://*:8001"];
  }
  const origin = new URL(configuredApi).origin;
  return [origin, origin.replace(/^http/, "ws")];
}

const api = apiSources().join(" ");
const servedOverHttps = Boolean(configuredApi?.startsWith("https://"));

const contentSecurityPolicy = [
  "default-src 'self'",
  // Next.js hydration uses inline scripts; React needs eval only in development.
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${api}`,
  "font-src 'self' data:",
  // Development also needs the hot-reload socket.
  `connect-src 'self' ${api}${isDev ? " ws: wss:" : ""}`,
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(servedOverHttps ? ["upgrade-insecure-requests"] : []),
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Microphone is used by dictation and voice mode on this site only.
  { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=(), payment=(), usb=()" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(servedOverHttps ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }] : []),
];

const nextConfig: NextConfig = {
  devIndicators: false,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // The site's front door is the staff sign-in; guests come in only through their table's QR code.
  async redirects() {
    return [
      { source: "/", destination: "/login", permanent: false },
      // Voice calls happen inside the chat now; an old link opens the menu (and its chat).
      { source: "/assistant/voice", destination: "/menu", permanent: false },
    ];
  },
};

export default nextConfig;
