"use client";

/**
 * The scene behind the sign-in and table-welcome pages (one scene, no half-and-half split):
 * a real photo of Jubran (the dining room, or the rooftop at sunset), under an even veil so
 * the card reads anywhere while the photo still shows around it (deeper at night).
 * The page it sits in is `relative isolate overflow-hidden`.
 */
export function HeritageScene({ photo = "lounge" }: { photo?: "lounge" | "rooftop" }) {
  return (
    <>
      {/* A CSS entrance, so the scene is there before the page's scripts arrive. */}
      <div className="animate-rise absolute inset-0 -z-20 [--rise-duration:1.6s] [--rise-from:0px] [--rise-scale:1.06]" aria-hidden="true">
        <div
          className="absolute inset-0 bg-cover bg-center dark:brightness-[0.6]"
          style={{ backgroundImage: `url('/backdrops/${photo}.webp')` }}
        />
      </div>
      <div className="absolute inset-0 -z-10 bg-[radial-gradient(75%_65%_at_50%_52%,color-mix(in_oklab,var(--canvas)_62%,transparent),color-mix(in_oklab,var(--canvas)_18%,transparent))] dark:bg-[radial-gradient(75%_65%_at_50%_52%,color-mix(in_oklab,var(--canvas)_72%,transparent),color-mix(in_oklab,var(--canvas)_40%,transparent))]" aria-hidden="true" />
      <div className="absolute inset-x-0 top-0 -z-10 h-28 bg-gradient-to-b from-canvas/80 to-transparent" aria-hidden="true" />
    </>
  );
}
