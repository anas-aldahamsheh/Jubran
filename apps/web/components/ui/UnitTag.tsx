/** How a line or a dish is served (plate, box, glass...), as a small tag next to its name. */
export function UnitTag({ label, className = "" }: { label: string | null | undefined; className?: string }) {
  if (!label) return null;
  return (
    <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-md border border-line bg-surface-2 px-1.5 py-0.5 align-middle text-[0.6875rem] font-semibold leading-none text-ink-2 ${className}`}>
      {label}
    </span>
  );
}
