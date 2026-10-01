"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useMotionValue } from "motion/react";
import { apiFetch, ApiException, getSocketUrl } from "@/lib/api";
import { speechLocale } from "@/lib/language";
import { type LiveOrderAction, LiveVoiceCall } from "@/lib/liveVoiceClient";
import { canRecordClips, canStreamMicrophone, recordUtterance } from "@/lib/voiceAudio";
import { useLanguage } from "@/context/LanguageContext";

/** What the waiter is doing during a call. */
export type CallState = "connecting" | "listening" | "thinking" | "speaking" | "reconnecting";

/** Why a call ended. The guest's own ways out (hang up, closing the chat, another page) need no note. */
export type CallEndReason =
  | "hang_up" | "closed_chat" | "changed_page"
  | "quiet" | "left_page" | "time_limit" | "connection_lost" | "provider_ended"
  | "microphone_blocked" | "rate_limited" | "session_ended" | "unsupported";

interface VoiceSession {
  mode: "live" | "turns";
  live_socket_path: string | null;
}

/** One whole step-by-step exchange: the same reply the text chat gets. */
export interface VoiceTurnResult {
  spoken_text: string;
  user_text?: string;
  draft?: unknown;
  action?: unknown;
}

export interface VoiceCallEvents {
  /** Live calls: words as they are heard and spoken (the guest's pieces join with a space). */
  onLiveText: (role: "user" | "assistant", chunk: string) => void;
  /** Live calls: the exchange is over; the next words start a new one. */
  onLiveTurnEnd: () => void;
  /** Live calls: a summary to confirm, an order sent, a change to confirm or applied. */
  onLiveAction: (action: LiveOrderAction) => void;
  /** Live calls: the confirm button could not send the order (the summary changed). */
  onLiveConfirmFailed: () => void;
  /** Step-by-step calls: one whole exchange. */
  onTurn: (result: VoiceTurnResult) => void;
  /** Something the guest should read in the conversation (a turn that failed). */
  onProblem: (message: string) => void;
  /** A calm note in the conversation (e.g. the call goes on step by step). */
  onNote: (message: string) => void;
  /** The call ended by itself: why, in words, and whether calling again makes sense. */
  onEnded: (reason: CallEndReason, message: string, canCallAgain: boolean) => void;
  /** The basket may have changed. */
  onDraftChanged: () => void;
}

/** A call where nothing happens for this long hangs up by itself (twice as long while muted),
 *  with a visible countdown during the last seconds; any word, tap or reply keeps it going. */
const IDLE_HANG_UP_S = 60;
const IDLE_WARNING_S = 15;
/** Leaving the page (another app, the screen switched off) ends the call only after this long. */
const AWAY_GRACE_MS = 20000;

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

type WakeLockLike = { release: () => Promise<void> };

/**
 * A voice call with the waiter inside the chat: live streaming when the restaurant has
 * it on, otherwise step by step (record, the server transcribes and answers, the phone
 * speaks). The same server, tools and guards as typing; only the way in and out differs.
 * The call keeps the screen on, rides out a dropped line (it reconnects), and ends when the
 * guest hangs up, closes the chat, stays away from the page, or nothing happens for a minute;
 * an ending the guest did not choose is explained in the conversation, with a way to call again.
 */
export function useVoiceCall(open: boolean, events: VoiceCallEvents) {
  const { lang, t } = useLanguage();
  const pathname = usePathname();
  const [active, setActive] = useState(false);
  const [state, setState] = useState<CallState>("connecting");
  const [muted, setMuted] = useState(false);
  const [tool, setTool] = useState<string | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [quietLeft, setQuietLeft] = useState<number | null>(null);
  const [audioPaused, setAudioPaused] = useState(false);
  // The microphone's loudness (0..1), for the call bar's waves without redrawing the chat.
  const level = useMotionValue(0);

  const eventsRef = useRef(events);
  const langRef = useRef(lang);
  const sessionRef = useRef<VoiceSession | null>(null);
  const activeRef = useRef(false);
  const mutedRef = useRef(false);
  const stateRef = useRef<CallState>("connecting");
  const liveRef = useRef<LiveVoiceCall | null>(null);
  const loopRef = useRef<AbortController | null>(null);
  const takeRef = useRef<AbortController | null>(null);
  const lastActivityRef = useRef(0);
  const wakeLockRef = useRef<WakeLockLike | null>(null);
  const awayTimerRef = useRef(0);
  useEffect(() => {
    eventsRef.current = events;
    langRef.current = lang;
  });

  const describe = useCallback((code: string) => {
    switch (code) {
      case "MICROPHONE_BLOCKED":
        return t("لم يُسمح باستخدام الميكروفون. اسمح به من إعدادات المتصفح، أو اكتب رسالتك.",
          "Microphone access was blocked. Allow it in the browser settings, or type your message.");
      case "UNSUPPORTED":
        return t("المحادثة الصوتية غير متاحة في هذا المتصفح. اكتب رسالتك بدلاً منها.",
          "Voice chat isn't available in this browser. Please type your message instead.");
      case "RATE_LIMITED":
        return t("مكالمات كثيرة خلال وقت قصير. انتظر دقيقة ثم حاول مرة أخرى.", "Too many calls in a short time. Please wait a minute.");
      case "ASSISTANT_BUSY":
        return t("المساعد ما زال يرد على رسالة سابقة. حاول بعد لحظة.", "The assistant is still answering a previous message. Try again shortly.");
      default:
        return t("تعذّر الاتصال بالمساعد الصوتي. حاول مرة أخرى.", "Couldn't reach the voice assistant. Please try again.");
    }
  }, [t]);

  const endingNote = useCallback((reason: CallEndReason): string => {
    switch (reason) {
      case "quiet":
        return t("سكّرت المكالمة لأنه ما صار في كلام من دقيقة.", "The call ended because it was quiet for a minute.");
      case "left_page":
        return t("انتهت المكالمة لأنك طلعت من الصفحة.", "The call ended because you left the page.");
      case "time_limit":
        return t("انتهت المكالمة لأنها وصلت أقصى مدة.", "The call reached its time limit.");
      case "connection_lost":
        return t("انقطع الاتصال بالمساعد الصوتي وما رجع.", "The connection to the voice assistant was lost.");
      case "provider_ended":
        return t("المساعد الصوتي وقف من جهته.", "The voice assistant stopped on its side.");
      case "microphone_blocked":
        return describe("MICROPHONE_BLOCKED");
      case "rate_limited":
        return describe("RATE_LIMITED");
      case "unsupported":
        return describe("UNSUPPORTED");
      default:
        return describe("CONNECTION_LOST");
    }
  }, [describe, t]);

  /** Something happened on the call: the idle countdown starts over. */
  const touch = useCallback(() => {
    lastActivityRef.current = performance.now();
  }, []);

  const enter = useCallback((next: CallState) => {
    stateRef.current = next;
    setState(next);
    lastActivityRef.current = performance.now();
  }, []);

  // The screen stays on during a call (the guest talks without touching it).
  const holdScreen = useCallback(async () => {
    if (wakeLockRef.current || typeof navigator === "undefined") return;
    const wakeLock = (navigator as Navigator & { wakeLock?: { request: (type: "screen") => Promise<WakeLockLike> } }).wakeLock;
    try {
      const sentinel = await wakeLock?.request("screen");
      if (!sentinel) return;
      if (activeRef.current) wakeLockRef.current = sentinel;
      else void sentinel.release().catch(() => undefined);
    } catch {
      // Not allowed or not supported: the call still works.
    }
  }, []);
  const releaseScreen = useCallback(() => {
    const sentinel = wakeLockRef.current;
    wakeLockRef.current = null;
    void sentinel?.release().catch(() => undefined);
  }, []);

  /** Hangs up and releases the microphone; an ending the guest did not choose is explained. */
  const finish = useCallback((reason: CallEndReason = "hang_up") => {
    if (!activeRef.current) return;
    activeRef.current = false;
    const live = liveRef.current;
    liveRef.current = null;
    live?.hangUp(reason);
    loopRef.current?.abort();
    loopRef.current = null;
    takeRef.current?.abort();
    takeRef.current = null;
    window.clearTimeout(awayTimerRef.current);
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    releaseScreen();
    level.set(0);
    mutedRef.current = false;
    setMuted(false);
    setTool(null);
    setIsLive(false);
    setQuietLeft(null);
    setAudioPaused(false);
    setActive(false);
    if (reason !== "hang_up" && reason !== "closed_chat" && reason !== "changed_page") {
      eventsRef.current.onEnded(reason, endingNote(reason), reason !== "session_ended" && reason !== "unsupported");
    }
  }, [endingNote, level, releaseScreen]);

  // The reply is read in its own language (the waiter answers in the guest's language).
  const speak = useCallback((text: string) => new Promise<void>((resolve) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window) || !text) return resolve();
    window.speechSynthesis.cancel();
    const arabic = /[؀-ۿ]/.test(text);
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = speechLocale(arabic ? "ar" : "en");
    const voice = window.speechSynthesis.getVoices().find((v) => v.lang.startsWith(arabic ? "ar" : "en"));
    if (voice) utterance.voice = voice;
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    window.speechSynthesis.speak(utterance);
  }), []);

  // --- Step by step ---------------------------------------------------------------
  const runTurns = useCallback(async () => {
    const loop = new AbortController();
    loopRef.current = loop;
    setIsLive(false);
    let quietRounds = 0;
    while (!loop.signal.aborted) {
      enter("listening");
      if (mutedRef.current) {
        await wait(250);
        continue;
      }
      const take = new AbortController();
      takeRef.current = take;
      const stopTake = () => take.abort();
      loop.signal.addEventListener("abort", stopTake, { once: true });
      let clip: Blob | null = null;
      try {
        clip = await recordUtterance({ onLevel: (value) => level.set(value), signal: take.signal });
      } catch {
        finish("microphone_blocked");
        return;
      } finally {
        loop.signal.removeEventListener("abort", stopTake);
        if (takeRef.current === take) takeRef.current = null;
      }
      if (loop.signal.aborted) return;
      level.set(0);
      if (!clip) {
        if (take.signal.aborted) continue; // muted mid-sentence: listen again once the mic is back
        quietRounds += 1;
        if (quietRounds >= 4) {
          finish("quiet"); // nobody is talking: hang up, the microphone closes
          return;
        }
        continue;
      }
      quietRounds = 0;
      enter("thinking");
      try {
        const form = new FormData();
        form.append("audio", clip, "turn.webm");
        const result = await apiFetch<VoiceTurnResult>(
          `/assistant/voice/audio-turn?language=${encodeURIComponent(langRef.current)}`,
          { method: "POST", body: form },
        );
        if (loop.signal.aborted) return;
        eventsRef.current.onTurn(result);
        eventsRef.current.onDraftChanged();
        enter("speaking");
        await speak(result.spoken_text);
      } catch (error) {
        if (loop.signal.aborted) return;
        // Nothing understandable was heard: just listen again.
        if (error instanceof ApiException && error.code === "EMPTY_VOICE_INPUT") continue;
        if (error instanceof ApiException && error.code === "RATE_LIMITED") {
          finish("rate_limited");
          return;
        }
        eventsRef.current.onProblem(error instanceof ApiException
          ? (langRef.current === "en" ? String(error.details?.message_en ?? error.message) : error.message)
          : describe("CONNECTION_LOST"));
      }
    }
  }, [describe, enter, finish, level, speak]);

  // --- Live -------------------------------------------------------------------------
  const runLive = useCallback((path: string) => {
    setIsLive(true);
    const call = new LiveVoiceCall(`${getSocketUrl(path)}?lang=${encodeURIComponent(langRef.current)}`, {
      onState: (next) => {
        if (!activeRef.current || liveRef.current !== call) return;
        if (next === "reconnecting") {
          // Words cut off by the drop stay as they are; what is said after it starts new bubbles.
          setTool(null);
          eventsRef.current.onLiveTurnEnd();
        }
        if (next !== "ended" && next !== "failed") enter(next); // endings come through onEnded / onError
      },
      onLevel: (value) => level.set(value),
      onActivity: touch,
      onAudioPaused: (paused) => {
        if (liveRef.current === call) setAudioPaused(paused);
      },
      onTranscript: (role, text) => eventsRef.current.onLiveText(role, text),
      onTurnComplete: () => {
        setTool(null);
        eventsRef.current.onLiveTurnEnd();
      },
      onTool: (name) => setTool(name),
      onAction: (action) => eventsRef.current.onLiveAction(action),
      onDraft: () => eventsRef.current.onDraftChanged(),
      onConfirmFailed: () => eventsRef.current.onLiveConfirmFailed(),
      onEnded: (reason) => {
        if (liveRef.current !== call) return;
        liveRef.current = null;
        finish(reason === "CALL_TIME_LIMIT" ? "time_limit" : "provider_ended");
      },
      onError: (code) => {
        if (liveRef.current !== call) return;
        liveRef.current = null;
        if (!activeRef.current) return;
        if (code === "MICROPHONE_BLOCKED") return finish("microphone_blocked");
        if (code === "RATE_LIMITED") return finish("rate_limited");
        if (code === "SESSION_ENDED") return finish("session_ended");
        if (code === "CONNECTION_LOST") return finish("connection_lost");
        // Live voice can't start right now: the same call carries on step by step, and says so.
        if (!canRecordClips()) return finish("unsupported");
        eventsRef.current.onNote(t("المحادثة الصوتية المباشرة مش متاحة هلأ، فرح نحكي خطوة بخطوة: احكي، واستنى الرد.",
          "Live voice isn't available right now, so we'll talk step by step: speak, then wait for the reply."));
        void runTurns();
      },
    });
    liveRef.current = call;
    void call.start();
  }, [enter, finish, level, runTurns, t, touch]);

  /** Starts a call; called from the guest's tap (browsers only allow the microphone and sound then). */
  const start = useCallback(async () => {
    if (activeRef.current) return;
    activeRef.current = true;
    mutedRef.current = false;
    setMuted(false);
    setTool(null);
    setQuietLeft(null);
    setAudioPaused(false);
    setActive(true);
    enter("connecting");
    void holdScreen();
    // iPhones only let a tap start speech: a silent word now lets the replies be read later.
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      const unlock = new SpeechSynthesisUtterance(" ");
      unlock.volume = 0;
      window.speechSynthesis.speak(unlock);
    }
    let session = sessionRef.current;
    if (!session) {
      session = await apiFetch<VoiceSession>("/assistant/voice/session", { method: "POST" }).catch(() => null);
      if (!activeRef.current) return;
      if (!session) {
        finish("connection_lost");
        return;
      }
      sessionRef.current = session;
    }
    if (session.mode === "live" && session.live_socket_path && canStreamMicrophone()) runLive(session.live_socket_path);
    else if (canRecordClips()) void runTurns();
    else finish("unsupported");
  }, [enter, finish, holdScreen, runLive, runTurns]);

  const end = useCallback(() => finish("hang_up"), [finish]);

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    liveRef.current?.setMuted(next);
    if (next) takeRef.current?.abort(); // step by step: drop the half-heard sentence
    lastActivityRef.current = performance.now();
  }, []);

  /** From a tap on the call bar: the phone paused its audio, resume it. */
  const resumeAudio = useCallback(() => {
    lastActivityRef.current = performance.now();
    void liveRef.current?.resumeAudio();
  }, []);

  /** Live calls: the summary's confirm button goes through the call, so the waiter knows. */
  const confirmLive = useCallback(() => {
    lastActivityRef.current = performance.now();
    return liveRef.current ? liveRef.current.confirmOrder() : false;
  }, []);

  // Knows how the restaurant offers voice before the guest taps, so the call starts at once.
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    void apiFetch<VoiceSession>("/assistant/voice/session", { method: "POST" })
      .then((session) => {
        if (!disposed) sessionRef.current = session;
      })
      .catch(() => undefined);
    return () => {
      disposed = true;
    };
  }, [open]);

  // Closing the chat or going to another page ends the call (the guest chose to).
  const pathRef = useRef(pathname);
  useEffect(() => {
    const changedPage = pathRef.current !== pathname;
    pathRef.current = pathname;
    if (!open || changedPage) void Promise.resolve().then(() => finish(changedPage ? "changed_page" : "closed_chat"));
  }, [open, pathname, finish]);

  // Away from the page (another app, the screen off): a short while is fine, then the call ends.
  useEffect(() => {
    const onVisibility = () => {
      if (!activeRef.current) return;
      if (document.visibilityState === "hidden") {
        window.clearTimeout(awayTimerRef.current);
        awayTimerRef.current = window.setTimeout(() => finish("left_page"), AWAY_GRACE_MS);
        return;
      }
      window.clearTimeout(awayTimerRef.current);
      lastActivityRef.current = performance.now();
      void holdScreen(); // the screen lock is released while away
      void liveRef.current?.resumeAudio();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearTimeout(awayTimerRef.current);
      finish("closed_chat");
    };
  }, [finish, holdScreen]);

  // Nothing happening for a minute: a countdown shows for the last seconds, then it hangs up.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      const busy = stateRef.current !== "listening";
      if (busy) {
        lastActivityRef.current = performance.now();
        setQuietLeft(null);
        return;
      }
      const limit = (mutedRef.current ? 2 : 1) * IDLE_HANG_UP_S;
      const idle = (performance.now() - lastActivityRef.current) / 1000;
      if (idle >= limit) {
        finish("quiet");
        return;
      }
      setQuietLeft(idle >= limit - IDLE_WARNING_S ? Math.ceil(limit - idle) : null);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active, finish]);

  return { active, state, muted, tool, isLive, level, quietLeft, audioPaused, start, end, toggleMute, confirmLive, resumeAudio, touch };
}
