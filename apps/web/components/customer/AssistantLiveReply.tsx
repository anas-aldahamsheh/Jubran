"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  BellOff, BellRing, BookOpenText, ChefHat, Hand, Lightbulb, type LucideIcon, MessageSquareWarning, Pencil, ReceiptText,
  ShoppingBasket, Sparkles, SprayCan, Star, Store, Timer, UtensilsCrossed,
} from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";

/** What the assistant is doing right now, as the server reports it while the reply is prepared. */
const STEPS: Record<string, { icon: LucideIcon; ar: string; en: string }> = {
  thinking: { icon: Sparkles, ar: "بفكّر", en: "Thinking" },
  menu: { icon: BookOpenText, ar: "بدوّر بالمنيو", en: "Checking the menu" },
  dish: { icon: UtensilsCrossed, ar: "بشوف تفاصيل الصنف", en: "Looking at the dish" },
  restaurant: { icon: Store, ar: "بشوف معلومات المطعم", en: "Checking the restaurant's details" },
  basket: { icon: ShoppingBasket, ar: "بشوف سلّتك", en: "Checking your basket" },
  adding: { icon: ShoppingBasket, ar: "بضيف للسلة", en: "Adding to your basket" },
  basket_update: { icon: ShoppingBasket, ar: "بعدّل سلّتك", en: "Updating your basket" },
  suggestion: { icon: Lightbulb, ar: "بختارلك إشي بيكمّل طلبك", en: "Finding something to go with it" },
  summary: { icon: ReceiptText, ar: "بجهّز ملخص طلبك", en: "Preparing your order summary" },
  sending: { icon: ChefHat, ar: "ببعت طلبك للمطبخ", en: "Sending your order to the kitchen" },
  status: { icon: Timer, ar: "بشوف وين وصل طلبك", en: "Checking on your order" },
  amendment: { icon: Pencil, ar: "بجهّز تعديل طلبك", en: "Preparing the change" },
  amending: { icon: Pencil, ar: "بعدّل طلبك", en: "Updating your order" },
  staff: { icon: BellRing, ar: "بنادي حدا من الموظفين", en: "Calling a staff member" },
  bill: { icon: ReceiptText, ar: "بطلبلك الحساب", en: "Asking for your bill" },
  tissues: { icon: Hand, ar: "بطلبلك محارم", en: "Asking for tissues" },
  cleaning: { icon: SprayCan, ar: "بطلب تنظيف الطاولة", en: "Asking to clean the table" },
  cancelling: { icon: BellOff, ar: "بلغي الطلب", en: "Cancelling the request" },
  complaint: { icon: MessageSquareWarning, ar: "بوصّل ملاحظتك للإدارة", en: "Passing your note to the management" },
  rating: { icon: Star, ar: "بسجّل تقييمك", en: "Saving your rating" },
};

/** The assistant's current step, in place of a typing indicator: one calm line that changes as it works. */
export function AssistantStep({ step }: { step: string }) {
  const { t } = useLanguage();
  const current = STEPS[step] ? step : "thinking";
  const { icon: Icon, ar, en } = STEPS[current];
  const label = `${t(ar, en)}…`;
  return (
    <motion.div
      layout
      transition={{ layout: { type: "spring", stiffness: 420, damping: 36 } }}
      role="status"
      aria-live="polite"
      className="flex h-11 items-center gap-2.5 overflow-hidden rounded-[1.25rem] rounded-es-md border border-line bg-surface ps-2.5 pe-4 shadow-card"
    >
      <span className="relative flex size-7 shrink-0 items-center justify-center" aria-hidden="true">
        {/* A ring that keeps turning while the assistant works. */}
        <motion.span
          className="absolute inset-0 rounded-full border-2 border-brand/15 border-t-brand"
          animate={{ rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
        />
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={current}
            initial={{ opacity: 0, scale: 0.4, rotate: -40 }}
            animate={{ opacity: 1, scale: 1, rotate: 0 }}
            exit={{ opacity: 0, scale: 0.4, rotate: 40 }}
            transition={{ type: "spring", stiffness: 520, damping: 30 }}
            className="flex"
          >
            <Icon className="size-3.5 text-brand" strokeWidth={2.4} />
          </motion.span>
        </AnimatePresence>
      </span>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={current}
          initial={{ opacity: 0, y: 12, filter: "blur(3px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -12, filter: "blur(3px)" }}
          transition={{ duration: 0.28, ease: [0.2, 0, 0, 1] }}
          className="animate-step-shimmer whitespace-nowrap text-[0.875rem] font-semibold"
        >
          {label}
        </motion.span>
      </AnimatePresence>
    </motion.div>
  );
}

/**
 * A reply that appears as it is written: the text arrives in pieces from the server and is
 * revealed smoothly, faster when more is waiting, with a cursor at the end until it is complete.
 */
export function WritingText({ text, streaming, onGrow, onDone }: {
  text: string;
  /** More text may still arrive. */
  streaming: boolean;
  /** The reply got longer on screen (to keep it in view). */
  onGrow: () => void;
  /** Everything is written and nothing more is coming. */
  onDone: () => void;
}) {
  const [shown, setShown] = useState(0);
  const latest = useRef({ text, streaming, onGrow, onDone });
  useLayoutEffect(() => {
    latest.current = { text, streaming, onGrow, onDone };
  });

  useEffect(() => {
    const instant = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;
    let previous = performance.now();
    let position = 0;
    let written = "";
    let finished = false;
    const tick = (now: number) => {
      const { text: target, streaming: more } = latest.current;
      // The final text can differ from what was streamed: keep only the part both share.
      if (!target.startsWith(written.slice(0, Math.floor(position)))) {
        let same = 0;
        while (same < position && target[same] === written[same]) same += 1;
        position = same;
      }
      written = target;
      const waiting = target.length - position;
      if (waiting > 0) {
        // About 55 letters a second, speeding up when more is waiting so it never falls behind.
        const speed = 55 + waiting * 5;
        position = instant ? target.length : Math.min(target.length, position + (speed * (now - previous)) / 1000);
        setShown(Math.floor(position));
        latest.current.onGrow();
      } else if (!more && !finished) {
        finished = true;
        latest.current.onDone();
      }
      previous = now;
      if (!finished) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <span dir="auto">
      {text.slice(0, shown)}
      <span
        className="ms-0.5 inline-block h-[1.05em] w-[3px] translate-y-[0.18em] animate-caret rounded-full bg-brand align-baseline"
        aria-hidden="true"
      />
    </span>
  );
}
