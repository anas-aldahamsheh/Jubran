"use client";

import { useEffect, useRef } from "react";

/** The guest's basket changed somewhere else on the page (e.g. the assistant added a dish). */
export const DRAFT_CHANGED_EVENT = "jubran:draft-changed";

export function announceDraftChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(DRAFT_CHANGED_EVENT));
}

/** Run `onChange` whenever another part of the page changed the basket. */
export function useDraftChanged(onChange: () => void): void {
  const handlerRef = useRef(onChange);
  useEffect(() => {
    handlerRef.current = onChange;
  });
  useEffect(() => {
    const listener = () => handlerRef.current();
    window.addEventListener(DRAFT_CHANGED_EVENT, listener);
    return () => window.removeEventListener(DRAFT_CHANGED_EVENT, listener);
  }, []);
}
