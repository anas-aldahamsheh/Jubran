"use client";

import Image from "next/image";
import { useLanguage } from "@/context/LanguageContext";

/**
 * The Jubran wordmark on a transparent background: Jubran navy in the light
 * theme, and lifted to near-white in the dark theme.
 * Size it with a height class (e.g. "h-9").
 */
export function Logo({ className = "h-9", priority = false }: { className?: string; priority?: boolean }) {
  const { t } = useLanguage();
  return (
    <span className={`relative inline-flex shrink-0 items-center ${className}`}>
      <Image
        unoptimized
        priority={priority}
        src="/brand/jubran-logo-light.webp"
        alt={t("جبران", "Jubran")}
        width={1164}
        height={400}
        draggable={false}
        className="h-full w-auto select-none dark:hidden"
      />
      <Image
        unoptimized
        priority={priority}
        src="/brand/jubran-logo-dark.webp"
        alt=""
        aria-hidden="true"
        width={1164}
        height={400}
        draggable={false}
        className="hidden h-full w-auto select-none dark:block"
      />
    </span>
  );
}
