"use client";

import { useMemo } from "react";
import { encodeQr, qrToSvgPath } from "@/lib/qr";

const QUIET_ZONE = 4;

export function QrCodeSvg({ value, label, className = "" }: { value: string; label: string; className?: string }) {
  const qr = useMemo(() => encodeQr(value, "M"), [value]);
  const dimension = qr.size + QUIET_ZONE * 2;

  return (
    <svg
      viewBox={`0 0 ${dimension} ${dimension}`}
      className={className}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect width={dimension} height={dimension} fill="#ffffff" />
      <path d={qrToSvgPath(qr, QUIET_ZONE)} fill="#000000" />
    </svg>
  );
}
