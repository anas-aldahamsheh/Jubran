import { Logo } from "@/components/ui/Logo";

/**
 * Opening a page that takes a moment: the thin line at the top runs at once, and if the
 * wait goes on the restaurant's mark appears with a quiet progress line (quick page
 * changes never flash it). Pages with their own loading.tsx show their shape instead.
 */
export default function Loading() {
  return (
    <div className="flex flex-1 flex-col" role="status" aria-busy="true">
      <div className="pointer-events-none fixed inset-x-0 top-0 z-[300] h-[3px] overflow-hidden" aria-hidden="true">
        <div className="h-full w-1/3 animate-route rounded-full bg-gradient-to-r from-brand via-leaf to-accent rtl:[animation-direction:reverse]" />
      </div>
      <div className="animate-late-in flex flex-1 flex-col items-center justify-center gap-5 p-6 [--late-delay:0.35s]">
        <Logo className="h-11" />
        <div className="h-1 w-36 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
          <div className="h-full w-1/3 animate-route rounded-full bg-brand rtl:[animation-direction:reverse]" />
        </div>
      </div>
    </div>
  );
}
