"use client";

/**
 * What needs a sound on the floor screen: orders waiting for approval, orders a
 * guest changed while the kitchen was preparing them, and open guest calls.
 */
export interface AlertSource {
  active_orders: Array<{ order_id: string; status: string; amendments?: Array<{ id: string; needs_attention: boolean }> }>;
  service_queue: Array<{ id: string; kind: string; status: string }>;
}

export interface AlertKeys {
  orders: Set<string>;
  calls: Set<string>;
}

export function alertKeys(snapshot: AlertSource): AlertKeys {
  return {
    orders: new Set([
      ...snapshot.active_orders.filter((order) => order.status === "PENDING_APPROVAL").map((order) => order.order_id),
      ...snapshot.active_orders.flatMap((order) => (order.amendments ?? [])
        .filter((change) => change.needs_attention).map((change) => `change:${change.id}`)),
    ]),
    calls: new Set(snapshot.service_queue.filter((item) => item.status === "OPEN").map((item) => `${item.kind}:${item.id}`)),
  };
}

/**
 * Which kinds of alert are new since the previous snapshot. Compares the actual
 * orders and requests, so a second order on an already-waiting table, or a new
 * order arriving while another is approved, still rings.
 */
export function newAlerts(previous: AlertKeys | null, next: AlertKeys): { order: boolean; call: boolean } {
  if (!previous) return { order: false, call: false }; // first load: nothing is "new"
  return {
    order: [...next.orders].some((key) => !previous.orders.has(key)),
    call: [...next.calls].some((key) => !previous.calls.has(key)),
  };
}

/** One audio context for the whole page (browsers limit how many may exist). */
export class AlertChime {
  private context: AudioContext | null = null;

  private audio(): AudioContext | null {
    if (typeof window === "undefined") return null;
    if (!this.context) {
      const AudioCtx = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtx) return null;
      this.context = new AudioCtx();
    }
    return this.context;
  }

  /** Call from a click or key press: browsers only start audio after a user gesture. */
  unlock(): void {
    const context = this.audio();
    if (context && context.state === "suspended") void context.resume().catch(() => undefined);
  }

  play(kind: "order" | "call"): void {
    const context = this.audio();
    if (!context) return;
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    try {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.connect(gain);
      gain.connect(context.destination);
      const now = context.currentTime;
      const [from, to, volume, length] = kind === "order" ? [587.33, 880, 0.25, 0.45] : [783.99, 1046.5, 0.22, 0.4];
      oscillator.frequency.setValueAtTime(from, now);
      oscillator.frequency.exponentialRampToValueAtTime(to, now + 0.15);
      gain.gain.setValueAtTime(volume, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + length);
      oscillator.start(now);
      oscillator.stop(now + length);
      oscillator.onended = () => {
        oscillator.disconnect();
        gain.disconnect();
      };
    } catch {
      // Audio unavailable: the screen still updates.
    }
  }

  close(): void {
    void this.context?.close().catch(() => undefined);
    this.context = null;
  }
}
