"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/** Which ends of a horizontal row still have hidden items. */
export interface ScrollRowEdges {
  start: boolean;
  end: boolean;
}

const DRAG_THRESHOLD = 6;

/**
 * A horizontal chip row that scrolls with every input, not only with touch:
 * the mouse wheel (vertical wheels move it sideways while it can still move),
 * dragging with the mouse, and `step` for arrow buttons. RTL rows (negative
 * scrollLeft) are handled, and `edges` says which arrows to show.
 */
export function useScrollRow<T extends HTMLElement>(ref: RefObject<T | null>) {
  const [edges, setEdges] = useState<ScrollRowEdges>({ start: false, end: false });
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const isRtl = () => getComputedStyle(el).direction === "rtl";
    const maxScroll = () => Math.max(0, el.scrollWidth - el.clientWidth);
    const position = () => Math.abs(el.scrollLeft);

    const update = () => {
      const max = maxScroll();
      const pos = position();
      setEdges((prev) => {
        const next = { start: pos > 2, end: max - pos > 2 };
        return prev.start === next.start && prev.end === next.end ? prev : next;
      });
    };

    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const max = maxScroll();
      if (max <= 0) return;
      const pos = position();
      const towardEnd = event.deltaY > 0;
      // At an end the page scrolls as usual.
      if ((towardEnd && max - pos <= 1) || (!towardEnd && pos <= 1)) return;
      event.preventDefault();
      const delta = event.deltaMode === 1 ? event.deltaY * 32 : event.deltaY;
      el.scrollLeft += isRtl() ? -delta : delta;
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType !== "mouse" || event.button !== 0 || maxScroll() <= 0) return;
      drag.current = { x: event.clientX, left: el.scrollLeft, moved: false };
    };
    const onPointerMove = (event: PointerEvent) => {
      const state = drag.current;
      if (!state) return;
      const dx = event.clientX - state.x;
      if (!state.moved && Math.abs(dx) < DRAG_THRESHOLD) return;
      if (!state.moved) {
        state.moved = true;
        el.setPointerCapture(event.pointerId);
        el.style.cursor = "grabbing";
      }
      el.scrollLeft = state.left - dx;
    };
    const endDrag = (event: PointerEvent) => {
      const state = drag.current;
      drag.current = null;
      el.style.cursor = "";
      if (state?.moved && el.hasPointerCapture(event.pointerId)) el.releasePointerCapture(event.pointerId);
      if (state?.moved) {
        // The release after a drag is not a click on the chip under the pointer.
        const swallow = (click: MouseEvent) => { click.preventDefault(); click.stopPropagation(); };
        el.addEventListener("click", swallow, { capture: true, once: true });
        window.setTimeout(() => el.removeEventListener("click", swallow, { capture: true }), 0);
      }
    };

    update();
    const resize = new ResizeObserver(update);
    resize.observe(el);
    const mutations = new MutationObserver(update);
    mutations.observe(el, { childList: true, subtree: true });
    el.addEventListener("scroll", update, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", endDrag);
    el.addEventListener("pointercancel", endDrag);
    return () => {
      resize.disconnect();
      mutations.disconnect();
      el.removeEventListener("scroll", update);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", endDrag);
      el.removeEventListener("pointercancel", endDrag);
    };
  }, [ref]);

  /** Move about one row-width toward the end (1) or the start (-1). */
  const step = useCallback((direction: 1 | -1) => {
    const el = ref.current;
    if (!el) return;
    const rtl = getComputedStyle(el).direction === "rtl";
    el.scrollBy({ left: (rtl ? -1 : 1) * direction * el.clientWidth * 0.75, behavior: "smooth" });
  }, [ref]);

  return { edges, step };
}
