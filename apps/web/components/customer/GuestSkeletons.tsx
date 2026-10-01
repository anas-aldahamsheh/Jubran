import { Skeleton } from "@/components/ui/Feedback";

/**
 * The shapes of the guest pages while they load. The same pieces are used by the pages
 * themselves and by their route loading screens, so opening a page goes from its shape to
 * its content without anything jumping.
 */

export function ProductCardSkeleton() {
  return (
    <div className="flex gap-3 rounded-3xl border border-line bg-surface p-3 @[30rem]:flex-col">
      <Skeleton className="size-24 shrink-0 rounded-2xl @[30rem]:aspect-[4/3] @[30rem]:size-auto @[30rem]:w-full" />
      <div className="flex-1 space-y-2.5 py-1">
        <Skeleton className="h-4 w-3/5" />
        <Skeleton className="h-3 w-2/5" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-8 w-24 rounded-full" />
      </div>
    </div>
  );
}

/** The dishes grid while the menu loads (inside the page's @container). */
export function MenuGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 @[30rem]:grid-cols-2 @[30rem]:gap-4 @[49rem]:grid-cols-3 @[49rem]:gap-5 @[68rem]:grid-cols-4">
      {Array.from({ length: count }, (_, index) => <ProductCardSkeleton key={index} />)}
    </div>
  );
}

/** The row of menu sections before they are known (chips, or the rail on wide screens). */
export function CategoryChipsSkeleton({ rail = false }: { rail?: boolean }) {
  const widths = rail ? ["w-full", "w-4/5", "w-3/4", "w-5/6", "w-2/3"] : ["w-28", "w-24", "w-32", "w-24", "w-28", "w-20"];
  return (
    <div className={rail ? "space-y-2 px-1" : "flex gap-2 overflow-hidden"} aria-hidden="true">
      {widths.map((width, index) => (
        <Skeleton key={index} className={rail ? `h-11 rounded-2xl ${width}` : `h-11 shrink-0 rounded-full ${width}`} />
      ))}
    </div>
  );
}

/** The menu page's opening, with its artwork, while the words and dishes load. */
function MenuHeroSkeleton() {
  return (
    <section className="relative isolate -mt-16 overflow-hidden md:-mt-[4.5rem]" aria-hidden="true">
      <div className="absolute inset-0 -z-20 bg-[url('/backdrops/rooftop.webp')] bg-cover bg-[center_45%] dark:brightness-[0.42] dark:saturate-[0.85]" />
      <div className="absolute inset-0 -z-10 bg-gradient-to-b from-canvas/10 via-canvas/45 to-canvas" />
      <div className="absolute inset-x-0 bottom-0 -z-10 h-24 bg-gradient-to-t from-canvas to-transparent" />
      <div className="mx-auto flex max-w-[1600px] flex-col items-center px-5 pb-12 pt-28 sm:pb-16 md:pt-36 lg:pb-20">
        <Skeleton className="h-7 w-56 rounded-full bg-surface/70" />
        <Skeleton className="mt-4 h-10 w-52 bg-surface/70 sm:h-14 sm:w-72 lg:h-16" />
        <Skeleton className="mt-3 h-4 w-72 max-w-full bg-surface/70" />
        <Skeleton className="mt-6 h-12 w-64 rounded-full bg-surface/70" />
      </div>
    </section>
  );
}

/** The whole menu page body while it opens (the frame with the top bar stays above it). */
export function MenuBodySkeleton() {
  return (
    <div className="flex-1 overflow-x-clip bg-canvas pb-28 text-ink md:pb-16" role="status" aria-busy="true">
      <MenuHeroSkeleton />
      <div className="mx-auto grid w-full max-w-[1600px] gap-8 px-4 sm:px-6 lg:px-8 2xl:grid-cols-[13.5rem_minmax(0,1fr)]">
        <aside className="hidden pt-9 2xl:block">
          <CategoryChipsSkeleton rail />
        </aside>
        <div className="@container min-w-0">
          <div className="-mx-4 mb-5 py-2.5 sm:-mx-6 lg:-mx-8 2xl:hidden">
            <div className="px-4 sm:px-6 lg:px-8"><CategoryChipsSkeleton /></div>
          </div>
          <MenuGridSkeleton />
        </div>
      </div>
    </div>
  );
}

/** Sent orders while they load. */
export function SentOrdersSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1].map((row) => (
        <div key={row} className="space-y-3 rounded-3xl border border-line bg-surface p-5">
          <div className="flex justify-between"><Skeleton className="h-5 w-28" /><Skeleton className="h-6 w-24 rounded-full" /></div>
          <Skeleton className="h-2 w-full rounded-full" />
          <Skeleton className="h-4 w-3/5" />
          <Skeleton className="h-4 w-2/5" />
        </div>
      ))}
    </div>
  );
}

/** The basket's rows while it loads. */
export function BasketRowsSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1].map((row) => (
        <div key={row} className="flex items-center gap-3 rounded-3xl border border-line bg-surface p-4">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-2/5" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="h-10 w-28 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/** The whole "my orders" page body while it opens. */
export function OrdersBodySkeleton() {
  return (
    <div className="flex flex-1 flex-col overflow-x-clip bg-canvas pb-28 text-ink md:pb-16" role="status" aria-busy="true">
      <section className="relative isolate -mt-16 overflow-hidden md:-mt-[4.5rem]" aria-hidden="true">
        <div className="absolute inset-0 -z-20 bg-[url('/backdrops/rooftop.webp')] bg-cover bg-[center_40%] opacity-70 dark:opacity-100 dark:brightness-[0.38] dark:saturate-[0.85]" />
        <div className="absolute inset-0 -z-10 bg-gradient-to-b from-canvas/30 via-canvas/75 to-canvas" />
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 pb-8 pt-24 sm:px-6 md:pt-32 lg:px-8">
          <Skeleton className="h-4 w-24 bg-surface/70" />
          <Skeleton className="h-11 w-48 bg-surface/70 sm:h-14" />
          <Skeleton className="h-4 w-80 max-w-full bg-surface/70" />
        </div>
      </section>
      <div className="mx-auto grid w-full max-w-6xl flex-1 gap-8 px-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:px-8" aria-hidden="true">
        <div className="flex min-w-0 flex-col gap-10">
          <div>
            <Skeleton className="mb-4 h-7 w-28" />
            <BasketRowsSkeleton />
          </div>
          <div>
            <Skeleton className="mb-4 h-7 w-40" />
            <SentOrdersSkeleton />
          </div>
        </div>
        <div className="space-y-3 rounded-[1.75rem] border border-line bg-surface p-5">
          <div className="flex items-center gap-3"><Skeleton className="size-11 rounded-2xl" /><Skeleton className="h-6 w-32" /></div>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-2">
            {[0, 1, 2, 3].map((cell) => <Skeleton key={cell} className="h-[5.5rem] rounded-2xl" />)}
          </div>
          <Skeleton className="h-12 w-full rounded-2xl" />
        </div>
      </div>
    </div>
  );
}
