"use client";

import "./globals.css";

/**
 * The very last safety net: the site's frame itself failed to show. It has its own page
 * (no language or theme settings reach it), so it speaks both languages.
 */
export default function GlobalError({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="ar" dir="rtl">
      <body className="flex min-h-dvh flex-col items-center justify-center bg-canvas p-6 text-center text-ink antialiased">
        <title>جبران | Jubran</title>
        <main className="w-full max-w-md rounded-[2rem] border border-line bg-surface p-7 shadow-float" role="alert">
          <h1 className="text-xl font-bold">صار خلل بسيط بفتح الموقع</h1>
          <p className="mt-1 text-sm text-muted" dir="ltr">Something went wrong opening the site.</p>
          <button type="button" onClick={() => retry()} className="btn btn-primary mt-6">
            حاول مرة ثانية · Try again
          </button>
        </main>
      </body>
    </html>
  );
}
