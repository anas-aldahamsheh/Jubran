"use client";

import { useEffect, useRef } from "react";
import { getSocketUrl } from "./api";

export interface LiveMessage {
  type: string;
  payload?: Record<string, unknown>;
}

/**
 * Keep a live-update WebSocket open (the login/visit cookie authenticates it).
 * Reconnects with a growing delay; `onReconnect` lets the page reload what it
 * may have missed. Pass `path = null` to stay disconnected.
 */
export function useLiveSocket(path: string | null, onMessage: (message: LiveMessage) => void,
                              onReconnect?: () => void): void {
  const onMessageRef = useRef(onMessage);
  const onReconnectRef = useRef(onReconnect);
  useEffect(() => {
    onMessageRef.current = onMessage;
    onReconnectRef.current = onReconnect;
  });

  useEffect(() => {
    if (!path) return;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      try {
        socket = new WebSocket(getSocketUrl(path));
      } catch {
        schedule();
        return;
      }
      socket.onopen = () => {
        if (attempt > 0) onReconnectRef.current?.();
        attempt = 0;
      };
      socket.onmessage = (event: MessageEvent) => {
        try {
          onMessageRef.current(JSON.parse(String(event.data)) as LiveMessage);
        } catch {
          // ignore malformed frames
        }
      };
      socket.onclose = () => {
        socket = null;
        schedule();
      };
    };
    const schedule = () => {
      if (disposed) return;
      const delay = Math.min(30_000, 1_000 * 2 ** attempt);
      attempt += 1;
      timer = setTimeout(connect, delay);
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [path]);
}
