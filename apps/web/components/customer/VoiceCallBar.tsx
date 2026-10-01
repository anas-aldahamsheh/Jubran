"use client";

import Image from "next/image";
import { AnimatePresence, motion, useTransform, type MotionValue } from "motion/react";
import { LoaderCircle, Mic, MicOff, PhoneOff, Volume2 } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { EASE_OUT, spring } from "@/lib/motion";
import type { CallState } from "./useVoiceCall";

// What the waiter is busy with while it thinks (the tool it is using right now).
const TOOL_STATUS: Record<string, [string, string]> = {
  search_knowledge: ["بدوّر بالقائمة…", "Checking the menu…"],
  get_product_details: ["بشوف تفاصيل الصنف…", "Looking at the dish…"],
  get_restaurant_info: ["بشوف معلومات المطعم…", "Checking the restaurant info…"],
  get_current_draft: ["براجع سلتك…", "Checking your basket…"],
  update_draft_order: ["بحدّث سلتك…", "Updating your basket…"],
  recommend_products: ["بختارلك اقتراح…", "Picking a suggestion…"],
  prepare_order_confirmation: ["بجهّز ملخص طلبك…", "Preparing your summary…"],
  submit_order: ["ببعت طلبك للمطبخ…", "Sending your order…"],
  get_order_status: ["بشوف وين وصل طلبك…", "Checking your order…"],
  prepare_order_amendment: ["بجهّز التعديل…", "Preparing the change…"],
  confirm_order_amendment: ["بعدّل طلبك…", "Updating your order…"],
  request_service: ["ببلّغ الموظفين…", "Letting the staff know…"],
  submit_complaint: ["بسجّل ملاحظتك…", "Noting your feedback…"],
  submit_feedback: ["بسجّل تقييمك…", "Saving your rating…"],
};

const WEIGHTS = [0.55, 0.85, 1, 0.85, 0.55];

/** One bar of the wave: the guest's voice while listening, a soft sway while the waiter speaks. */
function WaveBar({ index, level, state, muted }: { index: number; level: MotionValue<number>; state: CallState; muted: boolean }) {
  const weight = WEIGHTS[index];
  const heard = useTransform(level, (value) => Math.max(0.2, Math.min(1, 0.14 + value * 3.6 * weight)));
  if (state === "listening" && !muted) {
    return <motion.span style={{ scaleY: heard }} className="h-full w-[3px] origin-center rounded-full bg-success" />;
  }
  if (state === "speaking") {
    return (
      <motion.span
        animate={{ scaleY: [0.3, 0.55 + 0.45 * weight, 0.35, 0.4 + 0.5 * weight, 0.3] }}
        transition={{ duration: 1.1, repeat: Infinity, delay: index * 0.11, ease: "easeInOut" }}
        className="h-full w-[3px] origin-center rounded-full bg-accent"
      />
    );
  }
  return (
    <motion.span
      animate={{ scaleY: muted ? 0.2 : [0.2, 0.45, 0.2] }}
      transition={muted ? { duration: 0.2 } : { duration: 1.2, repeat: Infinity, delay: index * 0.1, ease: "easeInOut" }}
      className={`h-full w-[3px] origin-center rounded-full ${muted ? "bg-danger/60" : "bg-line-strong"}`}
    />
  );
}

/**
 * The composer during a voice call: who is talking, what the waiter is doing, mute and hang up.
 * It also says when the line is being restored, counts down before a quiet call hangs up, and
 * asks for a tap when the phone paused its audio.
 */
export function VoiceCallBar({ state, muted, tool, level, quietLeft = null, audioPaused = false, onToggleMute, onEnd, onResumeAudio }: {
  state: CallState;
  muted: boolean;
  tool: string | null;
  level: MotionValue<number>;
  /** Seconds left before a quiet call hangs up (shown only near the end). */
  quietLeft?: number | null;
  /** The phone paused the call's audio: a tap resumes it. */
  audioPaused?: boolean;
  onToggleMute: () => void;
  onEnd: () => void;
  onResumeAudio?: () => void;
}) {
  const { lang, t } = useLanguage();
  const busyWith = tool ? TOOL_STATUS[tool] : undefined;
  const status = audioPaused
    ? t("الصوت وقف، اضغط هون لإرجاعه", "Sound paused, tap here to resume")
    : state === "connecting"
      ? t("جاري الاتصال…", "Connecting…")
      : state === "reconnecting"
        ? t("بنرجع نوصل…", "Reconnecting…")
        : state === "listening"
          ? (quietLeft !== null
            ? t(`لسا معي؟ بسكّر بعد ${quietLeft} ث`, `Still there? Hanging up in ${quietLeft}s`)
            : muted ? t("المايك مكتوم", "Mic muted") : t("بسمعك…", "Listening…"))
          : state === "thinking"
            ? (busyWith ? busyWith[lang === "en" ? 1 : 0] : t("بفكّر…", "Thinking…"))
            : t("بحكي…", "Speaking…");

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 10, scale: 0.98 }}
      transition={spring.smooth}
      role="group"
      aria-label={t("مكالمة صوتية مع النادل", "Voice call with the waiter")}
      className={`relative flex items-center gap-1.5 overflow-hidden rounded-[1.5rem] border bg-canvas p-1.5 transition-colors duration-300 ${
        audioPaused || state === "reconnecting" ? "border-warning/55" : muted ? "border-danger/45" : state === "speaking" ? "border-accent/55" : state === "listening" ? "border-success/50" : "border-line-strong"
      }`}
    >
      {/* The last seconds before a quiet call hangs up run out along the bottom edge. */}
      <AnimatePresence>
        {quietLeft !== null && state === "listening" && !audioPaused && (
          <motion.span
            key="quiet"
            aria-hidden="true"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-x-0 bottom-0 h-0.5 origin-left bg-warning rtl:origin-right"
            style={{ transform: `scaleX(${Math.max(0, quietLeft) / 15})`, transition: "transform 1s linear" }}
          />
        )}
      </AnimatePresence>
      <span className="relative flex size-10 shrink-0 items-center justify-center">
        {state === "listening" && !muted && <span className="absolute inset-0 animate-ring rounded-full bg-success/45" aria-hidden="true" />}
        {state === "speaking" && (
          <motion.span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-accent/40"
            animate={{ scale: [1, 1.3, 1], opacity: [0.7, 0, 0.7] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeOut" }}
          />
        )}
        <Image
          src="/brand/jubran-emblem.webp"
          alt=""
          width={80}
          height={80}
          className={`relative size-10 rounded-full bg-gradient-to-br from-[#41579e] to-[#151e40] object-cover ring-2 ring-brand-line transition-opacity ${state === "reconnecting" ? "opacity-60" : ""}`}
        />
        {state === "reconnecting" && (
          <span className="absolute inset-0 flex items-center justify-center" aria-hidden="true">
            <LoaderCircle className="size-6 animate-spin text-white drop-shadow" />
          </span>
        )}
      </span>

      <div className="flex min-w-0 flex-1 items-center gap-2.5 px-1.5">
        <span className="flex h-5 shrink-0 items-center gap-[3px]" aria-hidden="true">
          {WEIGHTS.map((_, index) => <WaveBar key={index} index={index} level={level} state={state} muted={muted} />)}
        </span>
        <div className="relative h-6 min-w-0 flex-1 overflow-hidden" role="status" aria-live="polite">
          <AnimatePresence mode="wait" initial={false}>
            <motion.p
              key={audioPaused ? "paused" : quietLeft !== null && state === "listening" ? "quiet" : status}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2, ease: EASE_OUT }}
              className={`truncate text-sm font-semibold leading-6 ${
                audioPaused || state === "reconnecting" || (quietLeft !== null && state === "listening") ? "text-warning-ink" : muted && state === "listening" ? "text-danger-ink" : "text-ink"
              }`}
            >
              {status}
            </motion.p>
          </AnimatePresence>
        </div>
      </div>

      {audioPaused && onResumeAudio && (
        // Only a tap can resume the phone's audio: the whole bar is the button.
        <button
          type="button"
          onClick={onResumeAudio}
          aria-label={t("إرجاع الصوت", "Resume sound")}
          className="absolute inset-0 z-[1] flex items-center justify-end pe-28 text-warning-ink"
        >
          <Volume2 className="size-5 animate-pulse" aria-hidden="true" />
        </button>
      )}

      <button
        type="button"
        onClick={onToggleMute}
        style={{ position: "relative", zIndex: 2 }}
        aria-pressed={muted}
        aria-label={muted ? t("إلغاء كتم المايك", "Unmute the mic") : t("كتم المايك", "Mute the mic")}
        title={muted ? t("إلغاء كتم المايك", "Unmute the mic") : t("كتم المايك", "Mute the mic")}
        className={`flex size-10 shrink-0 items-center justify-center rounded-full transition-colors ${
          muted ? "bg-danger-soft text-danger" : "text-ink-2 hover:bg-surface-3 hover:text-ink"
        }`}
      >
        {muted ? <MicOff className="size-5" aria-hidden="true" /> : <Mic className="size-5" aria-hidden="true" />}
      </button>
      <motion.button
        type="button"
        onClick={onEnd}
        whileTap={{ scale: 0.9 }}
        style={{ position: "relative", zIndex: 2 }}
        aria-label={t("إنهاء المكالمة", "End the call")}
        title={t("إنهاء المكالمة", "End the call")}
        className="flex size-10 shrink-0 items-center justify-center rounded-full bg-danger text-white shadow-[0_6px_16px_-8px_var(--danger)] transition-colors hover:brightness-110"
      >
        <PhoneOff className="size-[18px]" aria-hidden="true" />
      </motion.button>
    </motion.div>
  );
}
