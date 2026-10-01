"use client";

import { useCallback, useState } from "react";
import { Soup } from "lucide-react";
import { getApiAssetUrl } from "@/lib/api";
import { useLanguage } from "@/context/LanguageContext";

type PhotoState = "loading" | "loaded" | "failed";

/**
 * A dish photo: a soft placeholder shimmers until the photo has arrived, then the photo
 * settles in. With no photo, or one that cannot be loaded, a quiet illustrated placeholder.
 */
export function ProductVisual({ url, alt, className = "", imgClassName = "", iconClassName = "size-9" }: {
  url?: string | null;
  alt: string;
  className?: string;
  imgClassName?: string;
  iconClassName?: string;
}) {
  const { t } = useLanguage();
  const src = url ? getApiAssetUrl(url) : null;
  // Remembered per address: another photo starts from "loading" again.
  const [photo, setPhoto] = useState<{ src: string | null; state: PhotoState }>({ src, state: "loading" });
  const state: PhotoState = photo.src === src ? photo.state : "loading";
  const settle = useCallback((next: PhotoState) => setPhoto({ src, state: next }), [src]);

  // A photo can finish before the page is interactive (from the cache, or while the page was
  // still starting), and then its load event is never seen: check once it is on the page.
  const checkFinished = useCallback((image: HTMLImageElement | null) => {
    if (!image?.complete) return;
    const next: PhotoState = image.naturalWidth > 0 ? "loaded" : "failed";
    queueMicrotask(() => settle(next));
  }, [settle]);

  return (
    <div className={`@container relative overflow-hidden bg-surface-3 ${className}`}>
      {src && state !== "failed" ? (
        <>
          {state === "loading" && <span className="skeleton absolute inset-0 rounded-none" aria-hidden="true" />}
          <span className={`absolute inset-0 transition-[opacity,transform] duration-500 ease-out-expo ${state === "loaded" ? "scale-100 opacity-100" : "scale-[1.04] opacity-0"}`}>
            {/* eslint-disable-next-line @next/next/no-img-element -- dish photos come from the API server (another origin, cached for a year) */}
            <img
              key={src}
              ref={checkFinished}
              src={src}
              alt={alt}
              loading="lazy"
              decoding="async"
              onLoad={() => settle("loaded")}
              onError={() => settle("failed")}
              className={`h-full w-full object-cover ${imgClassName}`}
            />
          </span>
        </>
      ) : (
        <div className="relative flex h-full w-full flex-col items-center justify-center gap-1.5 bg-gradient-to-br from-brand-soft to-accent-soft text-brand/70">
          <span className="heritage-pattern absolute inset-0 text-brand opacity-[0.1] [--pattern-size:30px]" aria-hidden="true" />
          <Soup className={`relative ${iconClassName}`} strokeWidth={1.5} aria-hidden="true" />
          {/* The words only where they fit on one line */}
          <span className="relative hidden whitespace-nowrap text-[0.625rem] font-semibold text-brand-soft-ink/80 @[6rem]:block">{t("صورة الطبق قريباً", "Photo coming soon")}</span>
          {src && <span className="sr-only">{alt}</span>}
        </div>
      )}
    </div>
  );
}
