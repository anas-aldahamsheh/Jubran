"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { GuestBottomNav, GuestTopBar } from "./GuestBottomNav";

const BasketCountContext = createContext<(count: number) => void>(() => undefined);

/**
 * The frame of the guest pages (the menu, my orders): the top bar and the phone's tab dock
 * stay in place while the pages change under them, and the basket count on them stays as
 * it was until the next page knows its own.
 */
export function GuestShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [count, setCount] = useState(0);
  const active = pathname.startsWith("/orders") ? "orders" : "menu";
  return (
    <BasketCountContext.Provider value={setCount}>
      <GuestTopBar active={active} itemCount={count} />
      {children}
      <GuestBottomNav active={active} itemCount={count} />
    </BasketCountContext.Provider>
  );
}

/** A guest page tells the frame how many dishes are in the basket. */
export function useGuestBasketCount(count: number): void {
  const setCount = useContext(BasketCountContext);
  useEffect(() => {
    setCount(count);
  }, [count, setCount]);
}
