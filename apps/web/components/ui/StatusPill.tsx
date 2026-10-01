export type PillTone = "neutral" | "brand" | "success" | "warning" | "danger" | "info" | "preparing" | "accent";

const TONES: Record<PillTone, { pill: string; dot: string }> = {
  neutral: { pill: "bg-surface-3 text-ink-2 border-line", dot: "bg-subtle" },
  brand: { pill: "bg-brand-soft text-brand-soft-ink border-brand-line", dot: "bg-brand" },
  success: { pill: "bg-success-soft text-success-ink border-success/25", dot: "bg-success" },
  warning: { pill: "bg-warning-soft text-warning-ink border-warning/25", dot: "bg-warning" },
  danger: { pill: "bg-danger-soft text-danger-ink border-danger/25", dot: "bg-danger" },
  info: { pill: "bg-info-soft text-info-ink border-info/25", dot: "bg-info" },
  preparing: { pill: "bg-preparing-soft text-preparing-ink border-preparing/25", dot: "bg-preparing" },
  accent: { pill: "bg-accent-soft text-accent-ink border-accent/30", dot: "bg-accent" },
};

/** A small coloured label for a state ("Preparing", "Ready"...), optionally with a live dot. */
export function StatusPill({ tone = "neutral", dot = false, pulse = false, size = "md", className = "", children }: {
  tone?: PillTone;
  dot?: boolean;
  pulse?: boolean;
  size?: "sm" | "md";
  className?: string;
  children: React.ReactNode;
}) {
  const colors = TONES[tone];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border font-semibold leading-none ${size === "sm" ? "px-2 py-1 text-[0.6875rem]" : "px-2.5 py-1.5 text-xs"} ${colors.pill} ${className}`}
    >
      {dot && (
        <span className="relative flex size-2 shrink-0" aria-hidden="true">
          {pulse && <span className={`absolute inset-0 animate-ping rounded-full opacity-60 ${colors.dot}`} />}
          <span className={`relative size-2 rounded-full ${colors.dot}`} />
        </span>
      )}
      {children}
    </span>
  );
}
