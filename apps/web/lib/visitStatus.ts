"use client";

import { useEffect, useRef } from "react";
import type { LiveMessage } from "./useLiveSocket";

/** The guest's table visit ended (staff closed the table, or it expired). */
export const VISIT_ENDED_EVENT = "jubran:visit-ended";
/** A live signal for the guest's table ("orders.changed", "visit.closed", "reconnected", ...). */
export const VISIT_SIGNAL_EVENT = "jubran:visit-signal";

let visitEnded = false;

export function announceVisitEnded(): void {
  if (typeof window === "undefined") return;
  visitEnded = true;
  window.dispatchEvent(new Event(VISIT_ENDED_EVENT));
}

/** A visit is active again (for example after scanning a new QR code). */
export function markVisitActive(): void {
  visitEnded = false;
}

/** True once the visit ended; polling loops use it to stop asking. */
export function hasVisitEnded(): boolean {
  return visitEnded;
}

export function announceVisitSignal(message: LiveMessage): void {
  window.dispatchEvent(new CustomEvent<LiveMessage>(VISIT_SIGNAL_EVENT, { detail: message }));
}

/** Receive the table's live signals (one shared connection, opened by VisitStatus). */
export function useVisitSignals(onSignal: (message: LiveMessage) => void): void {
  const handlerRef = useRef(onSignal);
  useEffect(() => {
    handlerRef.current = onSignal;
  });
  useEffect(() => {
    const listener = (event: Event) => handlerRef.current((event as CustomEvent<LiveMessage>).detail);
    window.addEventListener(VISIT_SIGNAL_EVENT, listener);
    return () => window.removeEventListener(VISIT_SIGNAL_EVENT, listener);
  }, []);
}
