import { Skeleton } from "@/components/ui/Feedback";

/**
 * The shapes of the admin pages while they load, used by the pages and by their route
 * loading screens (the admin header stays above them), so opening a page goes from its
 * shape to its content without anything jumping.
 */

function TitleSkeleton() {
  return (
    <div className="mb-6 flex items-start gap-4 border-b border-line pb-5">
      <Skeleton className="size-12 shrink-0 rounded-2xl" />
      <div className="flex-1 space-y-2.5 pt-1">
        <Skeleton className="h-7 w-72 max-w-[75%]" />
        <Skeleton className="h-4 w-[28rem] max-w-[92%]" />
      </div>
    </div>
  );
}

/** A queue of the live floor (service requests or orders) while it loads. */
export function QueueRowsSkeleton() {
  return (
    <div className="space-y-2.5">
      {[0, 1].map((row) => (
        <div key={row} className="space-y-2.5 rounded-2xl border border-line bg-surface-2 p-3.5">
          <div className="flex items-center justify-between gap-3">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-3.5 w-12" />
          </div>
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

export function FloorBodySkeleton() {
  return (
    <main className="mx-auto flex w-full max-w-[1720px] flex-1 flex-col gap-4 px-3 pb-28 pt-4 sm:px-5 lg:px-6 lg:pb-8" role="status" aria-busy="true">
      <section className="rounded-[1.75rem] border border-line bg-surface p-4 shadow-card sm:p-5" aria-hidden="true">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-center gap-3.5">
            <Skeleton className="size-12 shrink-0 rounded-2xl" />
            <div className="space-y-2">
              <Skeleton className="h-6 w-56" />
              <Skeleton className="h-4 w-44" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Skeleton className="h-10 w-48 rounded-xl" />
            <Skeleton className="size-10 rounded-xl" />
            <Skeleton className="size-10 rounded-xl" />
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
          {[0, 1, 2, 3, 4].map((kpi) => (
            <div key={kpi} className="flex items-center gap-3 rounded-2xl border border-line bg-surface-2 p-3 last:col-span-2 sm:last:col-span-1">
              <Skeleton className="size-10 shrink-0 rounded-xl" />
              <div className="flex-1 space-y-2"><Skeleton className="h-3 w-20" /><Skeleton className="h-5 w-12" /></div>
            </div>
          ))}
        </div>
      </section>
      <div className="flex gap-1.5 overflow-hidden" aria-hidden="true">
        {["w-16", "w-28", "w-28", "w-28", "w-20", "w-24", "w-28", "w-20"].map((width, index) => (
          <Skeleton key={index} className={`h-10 shrink-0 rounded-full ${width}`} />
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row" aria-hidden="true">
        <div className="relative flex min-h-[340px] flex-1 items-center justify-center overflow-hidden rounded-[1.75rem] border border-line bg-[#15191a] shadow-lift sm:min-h-[640px] xl:min-h-[700px]">
          <span className="size-12 animate-pulse rounded-full bg-white/10" />
        </div>
        <div className="flex w-full flex-col gap-4 lg:grid lg:grid-cols-2 xl:flex xl:w-[420px]">
          {[0, 1].map((panel) => (
            <section key={panel} className="flex flex-col gap-3 rounded-[1.75rem] border border-line bg-surface p-4 shadow-card xl:flex-1">
              <div className="flex items-center gap-2.5"><Skeleton className="size-9 rounded-xl" /><Skeleton className="h-5 w-32" /></div>
              <Skeleton className="h-14 w-full rounded-2xl" />
              <QueueRowsSkeleton />
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}

/** The restaurant details cards while they load. */
export function RestaurantFormSkeleton() {
  return (
    <div className="space-y-4">
      {[0, 1].map((card) => (
        <div key={card} className="space-y-4 rounded-[1.75rem] border border-line bg-surface p-6">
          <Skeleton className="h-6 w-48" />
          <div className="grid gap-4 md:grid-cols-2"><Skeleton className="h-11 w-full" /><Skeleton className="h-11 w-full" /></div>
          <Skeleton className="h-20 w-full" />
        </div>
      ))}
    </div>
  );
}

/** The dishes of the admin menu while they load. */
export function AdminDishesSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="flex gap-4 rounded-[1.5rem] border border-line bg-surface p-4">
          <Skeleton className="size-20 shrink-0 rounded-2xl" />
          <div className="flex-1 space-y-2.5"><Skeleton className="h-4 w-2/3" /><Skeleton className="h-3 w-1/3" /><Skeleton className="h-8 w-full rounded-xl" /></div>
        </div>
      ))}
    </div>
  );
}

export function AdminMenuBodySkeleton() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12" role="status" aria-busy="true">
      <div aria-hidden="true">
        <TitleSkeleton />
        <div className="mb-5 flex gap-2 overflow-hidden">
          {["w-24", "w-28", "w-24", "w-32"].map((width, index) => <Skeleton key={index} className={`h-10 shrink-0 rounded-full ${width}`} />)}
        </div>
        <AdminDishesSkeleton />
      </div>
    </main>
  );
}

/** The table cards (with their QR codes) while they load. */
export function TableCardsSkeleton() {
  return (
    <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="space-y-3 rounded-[1.75rem] border border-line bg-surface p-4">
          <div className="flex justify-between"><Skeleton className="h-8 w-16 rounded-xl" /><Skeleton className="h-6 w-20 rounded-full" /></div>
          <Skeleton className="aspect-square w-full rounded-2xl" />
          <Skeleton className="h-10 w-full rounded-xl" />
        </div>
      ))}
    </div>
  );
}

export function TablesBodySkeleton() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12" role="status" aria-busy="true">
      <div aria-hidden="true">
        <TitleSkeleton />
        <TableCardsSkeleton />
      </div>
    </main>
  );
}

/** The AI settings sections while they load. */
export function AiSectionsSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      {[0, 1, 2, 3].map((card) => (
        <div key={card} className="space-y-4 rounded-[1.75rem] border border-line bg-surface p-6">
          <div className="flex items-center gap-3"><Skeleton className="size-11 rounded-2xl" /><Skeleton className="h-5 w-40" /></div>
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-10 w-full rounded-2xl" />
          <Skeleton className="h-11 w-full rounded-xl" />
          <Skeleton className="h-11 w-full rounded-xl" />
        </div>
      ))}
    </div>
  );
}

export function AiSettingsBodySkeleton() {
  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12" role="status" aria-busy="true">
      <div aria-hidden="true">
        <TitleSkeleton />
        <AiSectionsSkeleton />
      </div>
    </main>
  );
}
