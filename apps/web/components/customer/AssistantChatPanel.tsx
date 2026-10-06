"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight, AudioLines, BookOpenText, Check, ChevronDown, CircleAlert, Clock3, Info, LoaderCircle, Mic, Pencil, PhoneOff,
  ReceiptText, SendHorizontal, ShoppingBag, Square, Timer, Trash2, X,
} from "lucide-react";
import { fadeUp, spring, stagger } from "@/lib/motion";
import { apiFetch, getCustomerSessionContext, ApiException } from "@/lib/api";
import { announceDraftChanged } from "@/lib/draftEvents";
import { hasVisitEnded, useVisitSignals } from "@/lib/visitStatus";
import { speechLocale } from "@/lib/language";
import { restaurantTime } from "@/lib/time";
import { canRecordClips, type Recording, startRecording } from "@/lib/voiceAudio";
import type { LiveOrderAction } from "@/lib/liveVoiceClient";
import { useLanguage } from "@/context/LanguageContext";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { useVoiceCall } from "./useVoiceCall";
import { ButtonSpinner, Skeleton } from "@/components/ui/Feedback";
import { VoiceCallBar } from "./VoiceCallBar";
import Image from "next/image";

type ChatDraft = {
  item_count: number;
  total_display_ar: string;
  total_display_en?: string;
  items: Array<{ name_ar: string; name_en?: string; quantity: number; line_total_display_ar: string; line_total_display_en?: string }>;
};

interface AmendmentChange {
  type: "added" | "removed" | "quantity_changed";
  name_ar: string;
  name_en?: string;
  quantity_before: number;
  quantity_after: number;
}

interface AmendmentSummary {
  order_number: string;
  changes: AmendmentChange[];
  total_after_display_ar: string;
  total_after_display_en?: string;
  cancels_order?: boolean;
  kitchen_already_preparing?: boolean;
}

interface ChatAction {
  type: string;
  request_id?: string;
  order_number?: string;
  amendment?: AmendmentSummary;
}

interface ConfirmResult {
  success: boolean;
  error_code?: string | null;
  error?: string | null;
  response?: string | null;
  action?: ChatAction | null;
  draft?: ChatDraft | null;
}

type ServerHistory = { history: Array<{ role: string; content: string }>; content_version: string };

/** The live voice's transcript can carry sound cues such as "[sigh]": they are heard, not read. */
const withoutSoundCues = (text: string) => text.replace(/\[[a-z][a-z _-]{0,20}\]\s*/gi, "");

interface ChatMessage {
  id: string;
  /** "notice": a calm line from the app itself (why a call ended, how the call goes on). */
  sender: "user" | "assistant" | "error" | "notice";
  text: string;
  /** A notice about a call that ended by itself: offer to call again. */
  callAgain?: boolean;
  draft?: ChatDraft;
  action?: ChatAction;
  table_number?: string;
  timestamp: string;
}

/** The conversation's shape while it loads: a few bubbles on both sides. */
function ChatSkeleton() {
  return (
    <div className="space-y-4 pt-1" aria-hidden="true">
      <div className="flex items-end gap-2">
        <Skeleton className="size-7 shrink-0 rounded-full" />
        <Skeleton className="h-16 w-[68%] rounded-[1.25rem] rounded-es-md" />
      </div>
      <div className="flex justify-end">
        <Skeleton className="h-10 w-[46%] rounded-[1.25rem] rounded-ee-md" />
      </div>
      <div className="flex items-end gap-2">
        <Skeleton className="size-7 shrink-0 rounded-full" />
        <Skeleton className="h-12 w-[58%] rounded-[1.25rem] rounded-es-md" />
      </div>
    </div>
  );
}

export function AssistantChatPanel({ open = true, onClose, onHeaderPointerDown }: {
  /** Whether the chat is showing (it stays mounted while closed). */
  open?: boolean;
  onClose: () => void;
  /** Phones: dragging the header down closes the chat. */
  onHeaderPointerDown?: (event: React.PointerEvent) => void;
}) {
  const { lang, dir, t } = useLanguage();
  const inLang = (ar: string, en?: string) => (lang === "en" && en ? en : ar);
  const { showAlert, showConfirm } = useGlobalDialog();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [inputMessage, setInputMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [tableNumber, setTableNumber] = useState<string | null>(null);
  const [restaurantName, setRestaurantName] = useState<{ ar: string; en: string } | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recordingRef = useRef<Recording | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Whether the guest is reading the latest messages (then they stay in view).
  const atBottomRef = useRef(true);
  const messagesRef = useRef<ChatMessage[]>([]);
  const sendingRef = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  // A live voice exchange being written as it is spoken: the guest's bubble and the waiter's.
  const liveTurnRef = useRef<{ userId: string | null; astId: string | null }>({ userId: null, astId: null });
  const callActiveRef = useRef(false);
  // The latest message the server's memory holds, as far as this chat knows. A live call shows
  // what the waiter said out loud, which differs from the words the server keeps, so the chat is
  // redrawn from the server only when the server's memory changed somewhere else (another tab).
  const serverTailRef = useRef<string | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const timeNow = () => restaurantTime(new Date(), speechLocale(lang), { hour: "2-digit", minute: "2-digit" });

  const addProblem = (text: string) =>
    setMessages((prev) => [...prev, { id: `err-${Date.now()}`, sender: "error", text, timestamp: timeNow() }]);
  const addNotice = (text: string, callAgain = false) =>
    setMessages((prev) => [...prev, { id: `note-${Date.now()}`, sender: "notice", text, callAgain, timestamp: timeNow() }]);

  // The live waiter's reply of this exchange (made now if it has not spoken yet).
  const updateLiveReply = (change: (message: ChatMessage) => ChatMessage) => {
    const turn = liveTurnRef.current;
    if (turn.astId) {
      const id = turn.astId;
      setMessages((prev) => prev.map((message) => (message.id === id ? change(message) : message)));
      return;
    }
    const id = `live-ast-${Date.now()}`;
    turn.astId = id;
    setMessages((prev) => [...prev, change({ id, sender: "assistant", text: "", timestamp: timeNow() })]);
  };

  // A voice call right inside the chat: what is said is written here as it is said, and the
  // summary and confirm cards are the same as when typing.
  const call = useVoiceCall(open, {
    // Pieces of speech join exactly as they come (they carry their own spaces).
    onLiveText: (role, chunk) => {
      if (!chunk) return;
      const turn = liveTurnRef.current;
      if (role === "assistant") {
        updateLiveReply((message) => ({ ...message, text: withoutSoundCues(message.text ? message.text + chunk : chunk.trimStart()) }));
        return;
      }
      if (turn.userId) {
        const id = turn.userId;
        setMessages((prev) => prev.map((message) => (message.id === id ? { ...message, text: message.text + chunk } : message)));
        return;
      }
      const id = `live-user-${Date.now()}`;
      turn.userId = id;
      const said: ChatMessage = { id, sender: "user", text: chunk.trimStart(), timestamp: timeNow() };
      // The guest's words go before the waiter's reply of the same exchange.
      setMessages((prev) => {
        const at = turn.astId ? prev.findIndex((message) => message.id === turn.astId) : -1;
        return at < 0 ? [...prev, said] : [...prev.slice(0, at), said, ...prev.slice(at)];
      });
    },
    onLiveTurnEnd: () => {
      const ids = [liveTurnRef.current.userId, liveTurnRef.current.astId];
      liveTurnRef.current = { userId: null, astId: null };
      setMessages((prev) => prev.map((message) => (ids.includes(message.id) ? { ...message, text: message.text.trim() } : message)));
    },
    onLiveAction: (action: LiveOrderAction) => {
      const amendment = (action as { amendment?: AmendmentSummary }).amendment;
      updateLiveReply((message) => ({
        ...message,
        action: { type: action.type, order_number: action.order_number, amendment },
        draft: action.type === "AWAITING_ORDER_CONFIRMATION" && action.summary ? (action.summary as ChatDraft) : message.draft,
      }));
      setConfirming(false);
      announceDraftChanged();
    },
    onLiveConfirmFailed: () => {
      setConfirming(false);
      addProblem(t("لم يُرسل الطلب: راجع الملخص الجديد.", "The order wasn't sent: please review the updated summary."));
    },
    onTurn: (result) => {
      const stamp = Date.now();
      setMessages((prev) => [
        ...prev,
        ...(result.user_text ? [{ id: `user-${stamp}`, sender: "user" as const, text: result.user_text, timestamp: timeNow() }] : []),
        {
          id: `ast-${stamp}`,
          sender: "assistant",
          text: result.spoken_text,
          draft: result.draft as ChatDraft | undefined,
          action: result.action as ChatAction | undefined,
          timestamp: timeNow(),
        },
      ]);
    },
    onProblem: addProblem,
    onNote: (text) => addNotice(text),
    onEnded: (_reason, text, canCallAgain) => addNotice(text, canCallAgain),
    onDraftChanged: announceDraftChanged,
  });

  useEffect(() => {
    const wasActive = callActiveRef.current;
    callActiveRef.current = call.active;
    if (call.active) return;
    liveTurnRef.current = { userId: null, astId: null };
    // The call's words are on screen; the server keeps its own wording of the same exchange.
    // Remember where the server's memory is now, so it is not redrawn over the call.
    if (wasActive) {
      void apiFetch<ServerHistory>("/assistant/history")
        .then((current) => {
          serverTailRef.current = current.history.at(-1)?.content ?? null;
        })
        .catch(() => undefined);
    }
  }, [call.active]);

  const startCall = () => {
    if (loading || call.active) return;
    // A dictation in progress gives way to the call.
    recordingRef.current?.cancel();
    recordingRef.current = null;
    setIsRecording(false);
    inputRef.current?.blur();
    void call.start();
  };

  // The conversation can also change elsewhere (another tab, clearing): redraw it from
  // the server's memory only when the latest message differs (never during a call, which
  // writes its own messages as they are said).
  const refreshHistory = async () => {
    if (sendingRef.current || callActiveRef.current || hasVisitEnded()) return;
    try {
      const current = await apiFetch<ServerHistory>("/assistant/history");
      if (sendingRef.current || callActiveRef.current) return;
      const lastOnServer = current.history.at(-1)?.content ?? null;
      const lastShown = messagesRef.current.filter((message) => message.sender === "user" || message.sender === "assistant").at(-1)?.text ?? null;
      const known = serverTailRef.current;
      serverTailRef.current = lastOnServer;
      if (lastOnServer !== lastShown && lastOnServer !== known) {
        setMessages(current.history.map((message, index) => ({
          id: `hist-${Date.now()}-${index}`,
          sender: message.role === "user" ? "user" : "assistant",
          text: message.content,
          timestamp: "",
        })));
      }
    } catch {
      // Keep the displayed conversation until the connection returns.
    }
  };

  useEffect(() => {
    let disposed = false;

    // Load initial context and session history
    const initChat = async () => {
      // The chat belongs to a table visit (kept by the browser in an HttpOnly cookie).
      const contextData = await getCustomerSessionContext().catch(() => null);
      if (disposed) return;
      if (!contextData) {
        setHistoryLoaded(true);
        return;
      }
      setTableNumber(contextData.table_number);
      try {
        const [histData, restaurantData] = await Promise.all([
          apiFetch<ServerHistory>("/assistant/history").catch(() => ({ history: [], content_version: "" })),
          apiFetch<{ name_ar: string; name_en: string }>("/restaurant").catch(() => null),
        ]);
        if (disposed) return;
        if (restaurantData) setRestaurantName({ ar: restaurantData.name_ar, en: restaurantData.name_en });

        serverTailRef.current = histData.history?.at(-1)?.content ?? null;
        if (histData.history && histData.history.length > 0) {
          // The server keeps no times for earlier messages, so none is shown.
          const mapped: ChatMessage[] = histData.history.map((m, idx) => ({
            id: `hist-${idx}`,
            sender: m.role === "user" ? "user" : "assistant",
            text: m.content,
            timestamp: "",
          }));
          setMessages(mapped);
        } else {
          setMessages([]);
        }
      } catch (err) {
        console.error("Error loading chat context:", err);
      } finally {
        if (!disposed) setHistoryLoaded(true);
      }
    };

    void initChat();
    return () => {
      disposed = true;
    };
  }, []);

  // No polling: the table's live connection says when this guest's conversation changed
  // (voice mode, another tab); coming back to the tab also re-checks.
  useVisitSignals((message) => {
    if (message.type === "conversation.changed" || message.type === "reconnected") void refreshHistory();
  });
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshHistory();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // New messages scroll the conversation itself (never the page, which made phones jump).
  useEffect(() => {
    const list = listRef.current;
    list?.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  // When the keyboard opens (the conversation gets shorter), the latest message stays right above it.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const observer = new ResizeObserver(() => {
      if (atBottomRef.current) list.scrollTop = list.scrollHeight;
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, []);

  // Computers: the chat opens ready to type (phones wait for a tap, so the keyboard doesn't cover it).
  useEffect(() => {
    if (!open || !window.matchMedia("(pointer: fine)").matches) return;
    // Right after the panel starts showing (it is hidden, so not focusable, a moment before).
    const timer = window.setTimeout(() => inputRef.current?.focus({ preventScroll: true }), 80);
    return () => window.clearTimeout(timer);
  }, [open]);

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputMessage).trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: "user",
      text: text,
      timestamp: timeNow(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputMessage("");
    setLoading(true);
    sendingRef.current = true;

    try {
      const response = await apiFetch<{
        response: string;
        draft?: ChatDraft;
        action?: ChatAction;
        table_number: string;
      }>("/assistant/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, language: lang }),
      });

      if (response.table_number) {
        setTableNumber(response.table_number);
      }


      // The reply appears word by word, as if being written.
      const id = `ast-${Date.now()}`;
      const words = response.response.split(/(\s+)/);
      let shown = 1;
      const timer = window.setInterval(() => {
        shown += 2;
        const text = words.slice(0, shown).join("");
        setMessages((prev) => prev.map((message) => (message.id === id ? { ...message, text } : message)));
        if (shown >= words.length) window.clearInterval(timer);
      }, 35);
      const assistantMsg: ChatMessage = {
        id,
        sender: "assistant",
        text: words[0] ?? "",
        draft: response.draft,
        action: response.action,
        table_number: response.table_number,
        timestamp: timeNow(),
      };

      setMessages((prev) => [...prev, assistantMsg]);
      serverTailRef.current = response.response;
      // The assistant may have changed the basket: the orders page and menu badge follow.
      announceDraftChanged();
    } catch (err: unknown) {
      const errMsg = err instanceof ApiException
        ? (lang === "en" ? String(err.details?.message_en ?? err.message) : err.message)
        : t("حدث خطأ في الاتصال، يرجى المحاولة مرة أخرى.", "Connection problem. Please try again.");
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: "error",
          text: errMsg,
          timestamp: timeNow(),
        },
      ]);
    } finally {
      sendingRef.current = false;
      setLoading(false);
    }
  };

  const confirmErrorText = (code?: string | null) => {
    if (code === "ORDER_ALREADY_SUBMITTED") return t("هذا الطلب أُرسل بالفعل.", "This order was already sent.");
    if (code === "EMPTY_DRAFT") return t("سلتك فارغة الآن، لا يوجد ما يُرسل.", "Your basket is empty now; there is nothing to send.");
    if (code === "NOTHING_TO_SEND") return t("طلبك انبعت، وما في شي جديد بالسلة يُرسل.", "Your order was already sent; there is nothing new in the basket to send.");
    return t("لا يوجد ملخص بانتظار التأكيد. اطلب من المساعد ملخص طلبك أولاً.",
      "There is no summary waiting for confirmation. Ask the assistant to summarize your order first.");
  };

  // "Confirm" on the summary card submits exactly the summary the assistant showed, no
  // second review page. During a live call it goes through the call, so the waiter knows.
  const handleConfirmSummary = async () => {
    if (confirming || loading) return;
    setConfirming(true);
    if (call.isLive && call.confirmLive()) {
      // The call answers with the order sent (or why not); never stay stuck if it doesn't.
      window.setTimeout(() => setConfirming(false), 10000);
      return;
    }
    sendingRef.current = true;
    try {
      const result = await apiFetch<ConfirmResult>("/assistant/confirm-order", {
        method: "POST",
        body: JSON.stringify({ language: lang }),
      });
      announceDraftChanged();
      if (result.action?.type === "ORDER_SUBMITTED") {
        setMessages((prev) => [
          ...prev,
          { id: `user-${Date.now()}`, sender: "user", text: t("✅ (تأكيد الطلب بالزر)", "✅ (confirmed with the button)"), timestamp: timeNow() },
          { id: `ast-${Date.now()}`, sender: "assistant", text: result.response ?? "", action: result.action ?? undefined, timestamp: timeNow() },
        ]);
        return;
      }
      const changed = result.action?.type === "AWAITING_ORDER_CONFIRMATION";
      setMessages((prev) => [...prev, {
        id: `ast-${Date.now()}`,
        sender: changed ? "assistant" : "error",
        text: changed
          ? t("تغيّر طلبك أو أسعاره منذ الملخص. راجع الملخص المحدّث ثم أكّد من جديد.",
            "Your order or its prices changed since the summary. Please review the updated summary and confirm again.")
          : (lang === "ar" && result.error) || confirmErrorText(result.error_code),
        draft: changed ? result.draft ?? undefined : undefined,
        action: changed ? result.action ?? undefined : undefined,
        timestamp: timeNow(),
      }]);
    } catch (err: unknown) {
      setMessages((prev) => [...prev, {
        id: `err-${Date.now()}`,
        sender: "error",
        text: err instanceof ApiException ? err.message : t("حدث خطأ في الاتصال، يرجى المحاولة مرة أخرى.", "Connection problem. Please try again."),
        timestamp: timeNow(),
      }]);
    } finally {
      sendingRef.current = false;
      setConfirming(false);
    }
  };

  // Only the latest summary can still be confirmed.
  const activeSummaryId = (() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const type = messages[index].action?.type;
      if (type === "AWAITING_ORDER_CONFIRMATION") return messages[index].id;
      if (type === "ORDER_SUBMITTED") return null;
    }
    return null;
  })();

  // Only the latest proposed change to a sent order can still be confirmed.
  const activeAmendmentId = (() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const type = messages[index].action?.type;
      if (type === "AWAITING_AMENDMENT_CONFIRMATION") return messages[index].id;
      if (type === "ORDER_AMENDED") return null;
    }
    return null;
  })();

  const amendmentErrorText = (code?: string | null) => {
    if (code === "ORDER_LOCKED") return t("الطلب صار جاهز أو تسلّم، فما بنقدر نعدّل عليه. أي أصناف إضافية بتصير طلب جديد.",
      "The order is ready or served now, so it can't be changed. Extra dishes go in a new order.");
    return t("لا يوجد تعديل بانتظار التأكيد. اطلب التعديل من المساعد أولاً.",
      "There is no change waiting for confirmation. Ask the assistant for the change first.");
  };

  // "Confirm" on a change card applies exactly the change the assistant showed.
  const handleConfirmAmendment = async () => {
    if (confirming || loading) return;
    setConfirming(true);
    sendingRef.current = true;
    try {
      const result = await apiFetch<ConfirmResult>("/assistant/confirm-amendment", {
        method: "POST",
        body: JSON.stringify({ language: lang }),
      });
      if (result.action?.type === "ORDER_AMENDED") {
        setMessages((prev) => [
          ...prev,
          { id: `user-${Date.now()}`, sender: "user", text: t("✅ (تأكيد التعديل بالزر)", "✅ (confirmed with the button)"), timestamp: timeNow() },
          { id: `ast-${Date.now()}`, sender: "assistant", text: result.response ?? "", action: result.action ?? undefined, timestamp: timeNow() },
        ]);
        return;
      }
      const again = result.action?.type === "AWAITING_AMENDMENT_CONFIRMATION";
      setMessages((prev) => [...prev, {
        id: `ast-${Date.now()}`,
        sender: again ? "assistant" : "error",
        text: again
          ? t("الطلب تغيّر من شوي. راجع التعديل المحدّث ثم أكّد من جديد.", "The order changed a moment ago. Review the updated change and confirm again.")
          : amendmentErrorText(result.error_code),
        action: again ? result.action ?? undefined : undefined,
        timestamp: timeNow(),
      }]);
    } catch (err: unknown) {
      setMessages((prev) => [...prev, {
        id: `err-${Date.now()}`,
        sender: "error",
        text: err instanceof ApiException ? err.message : t("حدث خطأ في الاتصال، يرجى المحاولة مرة أخرى.", "Connection problem. Please try again."),
        timestamp: timeNow(),
      }]);
    } finally {
      sendingRef.current = false;
      setConfirming(false);
    }
  };

  const changeLine = (change: AmendmentChange) => {
    const name = inLang(change.name_ar, change.name_en);
    if (change.type === "added") return `+ ${change.quantity_after} × ${name}`;
    if (change.type === "removed") return t(`− حذف ${name}`, `− Remove ${name}`);
    return `${name}: ${change.quantity_before} → ${change.quantity_after}`;
  };

  const handleClearHistory = async () => {
    if (clearing) return;
    if (!await showConfirm(t("هل ترغب في مسح محادثتك الحالية؟", "Clear your current conversation?"), { title: t("مسح المحادثة", "Clear conversation"), tone: "danger", confirmLabel: t("مسح", "Clear") })) return;
    try {
      setClearing(true);
      await apiFetch("/assistant/clear-history", { method: "POST" });
      setMessages([]);
    } catch (err) {
      console.error("Error clearing history:", err);
    } finally {
      setClearing(false);
    }
  };

  // Dictation (REQ-017): record, let the server's configured model transcribe it,
  // and put the text in the box for the guest to check and send. Never auto-sends.
  const toggleDictation = async () => {
    if (transcribing) return;
    if (recordingRef.current) {
      const recording = recordingRef.current;
      recordingRef.current = null;
      setIsRecording(false);
      const clip = await recording.stop();
      if (!clip) return;
      setTranscribing(true);
      try {
        const form = new FormData();
        form.append("audio", clip, "dictation.webm");
        const { transcript } = await apiFetch<{ transcript: string }>(
          `/assistant/dictation?language=${encodeURIComponent(lang)}`,
          { method: "POST", body: form },
        );
        if (transcript?.trim()) {
          setInputMessage((prev) => (prev.trim() ? `${prev.trim()} ${transcript.trim()}` : transcript.trim()));
        }
      } catch (err) {
        // Whatever is already typed stays as it is.
        void showAlert(err instanceof ApiException
          ? err.message
          : t("تعذّر تحويل التسجيل إلى نص. حاول مرة أخرى.", "Couldn't turn the recording into text. Please try again."));
      } finally {
        setTranscribing(false);
      }
      return;
    }
    if (!canRecordClips()) {
      void showAlert(t("التسجيل الصوتي غير متاح في هذا المتصفح أو على اتصال غير آمن (HTTP).",
        "Voice recording isn't available in this browser or on an insecure (HTTP) connection."));
      return;
    }
    try {
      recordingRef.current = await startRecording(60000);
      setIsRecording(true);
    } catch {
      void showAlert(t("لم يُسمح باستخدام الميكروفون. اسمح به من إعدادات المتصفح.",
        "Microphone access was blocked. Allow it in the browser settings."));
    }
  };

  // Release the microphone if the panel closes while recording.
  useEffect(() => () => recordingRef.current?.cancel(), []);
  const welcomeTopics = [
    { icon: BookOpenText, text: t("القائمة والأسعار والتوفر", "Menu, prices and availability") },
    { icon: ShoppingBag, text: t("اختيار الأصناف وتأكيد الطلب", "Choosing dishes and confirming") },
    { icon: Timer, text: t("حالة طلبك وخدمة الطاولة", "Your order status and table service") },
    { icon: Clock3, text: t("الدوام ومعلومات الفرع", "Opening hours and branch info") },
  ];
  const suggestions = [t("شو عندكم؟", "What's on the menu?"), t("شو بتنصحني؟", "What do you recommend?"),
    t("وين وصل طلبي؟", "Where's my order?"), t("بدي موظف لو سمحت", "Can someone come to my table?")];

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas" dir={dir}>
      {/* Header */}
      <header
        onPointerDown={onHeaderPointerDown}
        className={`relative z-[5] flex shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-4 pb-3 backdrop-blur-xl ${onHeaderPointerDown ? "touch-none pt-6 group-data-[typing]/assistant:pt-3" : "pt-4"}`}
      >
        <span className="relative shrink-0">
          <Image src="/brand/jubran-emblem.webp" alt="" width={96} height={96} className="size-11 rounded-full bg-gradient-to-br from-[#41579e] to-[#151e40] object-cover ring-2 ring-brand-line" />
          <span className="absolute -bottom-0.5 -end-0.5 flex size-3.5 items-center justify-center rounded-full bg-surface" aria-hidden="true">
            <span className="size-2 rounded-full bg-success" />
          </span>
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[0.9375rem] font-bold leading-tight text-ink">
            {t(`مساعد ${restaurantName?.ar || "جبران"}`, `${restaurantName?.en || "Jubran"} Assistant`)}
          </h1>
          <div className="mt-0.5 h-5 overflow-hidden text-xs leading-5">
            <AnimatePresence mode="wait" initial={false}>
              {call.active ? (
                <motion.p key="call" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }} className="flex items-center gap-1.5 truncate font-semibold text-success">
                  <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-success" aria-hidden="true" />
                  {t("مكالمة صوتية", "Voice call")}
                </motion.p>
              ) : loading ? (
                <motion.p key="typing" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }} className="truncate font-semibold text-brand">
                  {t("يكتب…", "typing…")}
                </motion.p>
              ) : (
                <motion.p key="online" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }} className="flex items-center gap-1.5 truncate text-muted">
                  <span className="font-semibold text-success">{t("متصل", "Online")}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{tableNumber ? t(`طاولة ${tableNumber}`, `Table ${tableNumber}`) : t("نادلك الذكي", "Your smart waiter")}</span>
                </motion.p>
              )}
            </AnimatePresence>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5" onPointerDown={(event) => event.stopPropagation()}>
          <button
            type="button"
            onClick={handleClearHistory}
            disabled={clearing}
            aria-busy={clearing}
            className="flex size-10 items-center justify-center rounded-full text-muted transition-colors hover:bg-danger-soft hover:text-danger"
            title={t("مسح المحادثة", "Clear conversation")}
            aria-label={t("مسح المحادثة", "Clear conversation")}
          >
            {clearing ? <ButtonSpinner className="size-[18px]" /> : <Trash2 className="size-[18px]" aria-hidden="true" />}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("إغلاق المحادثة", "Close chat")}
            title={t("إغلاق المحادثة", "Close chat")}
            className="flex size-10 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-3 hover:text-ink"
          >
            {onHeaderPointerDown ? <ChevronDown className="size-6" aria-hidden="true" /> : <X className="size-5" aria-hidden="true" />}
          </button>
        </div>
      </header>

      {/* Messages */}
      <div
        ref={listRef}
        onScroll={(event) => {
          const list = event.currentTarget;
          atBottomRef.current = list.scrollHeight - list.scrollTop - list.clientHeight < 64;
        }}
        onPointerDown={(event) => {
          if (call.active) call.touch();
          // Phones: touching the conversation puts the keyboard away, to read more of it.
          if (event.pointerType === "touch" && document.activeElement === inputRef.current && !(event.target as Element).closest("button, a")) {
            inputRef.current?.blur();
          }
        }}
        className="relative min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-3.5 py-4 touch-scroll sm:px-4"
      >
        <AnimatePresence initial={false} mode="popLayout">
          {!historyLoaded && (
            <motion.div key="chat-loading" exit={{ opacity: 0, transition: { duration: 0.2 } }} role="status" aria-label={t("جاري تحميل المحادثة...", "Loading the conversation...")}>
              <ChatSkeleton />
            </motion.div>
          )}
        </AnimatePresence>

        {historyLoaded && messages.length === 0 && (
          <motion.section
            initial="hidden"
            animate="show"
            variants={stagger(0.05, 0.06)}
            className="overflow-hidden rounded-3xl border border-line bg-surface shadow-card"
            aria-label={t("رسالة ترحيب من المساعد", "Assistant welcome message")}
          >
            <motion.div variants={fadeUp} className="relative flex items-end gap-3 overflow-hidden bg-gradient-to-br from-brand-soft via-surface to-accent-soft px-4 pt-4">
              <span className="heritage-pattern pointer-events-none absolute inset-0 text-brand opacity-[0.07]" aria-hidden="true" />
              <div className="relative min-w-0 flex-1 pb-4">
                <h2 className="font-display text-xl font-bold leading-snug text-ink">{t("أهلاً بك في جبران", "Welcome to Jubran")}</h2>
                <p className="mt-1 text-sm leading-relaxed text-muted">{t("احكيلي شو بتحتاج، وأنا بساعدك بكل سرور.", "Tell me what you need, and I will be happy to help.")}</p>
              </div>
              <Image src="/brand/jubran-emblem-ring.webp" alt="" width={256} height={256} className="relative mb-4 w-20 shrink-0 select-none rounded-full shadow-card" draggable={false} />
            </motion.div>
            <div className="px-4 pb-4 pt-3.5">
              {/* While typing on a phone only the quick suggestions stay, right above the keyboard. */}
              <motion.p variants={fadeUp} className="mb-2.5 text-xs font-semibold tracking-wide text-subtle group-data-[typing]/assistant:hidden">{t("أقدر أساعدك في", "I can help with")}</motion.p>
              <ul className="grid grid-cols-2 gap-2 group-data-[typing]/assistant:hidden">
                {welcomeTopics.map((topic) => (
                  <motion.li key={topic.text} variants={fadeUp} className="flex items-start gap-2 rounded-2xl bg-surface-2 p-2.5 text-[0.8125rem] leading-snug text-ink-2">
                    <topic.icon className="mt-0.5 size-4 shrink-0 text-brand" aria-hidden="true" />
                    <span>{topic.text}</span>
                  </motion.li>
                ))}
              </ul>
              {/* Dimmed here while they can't be used (the buttons' own opacity is animated). */}
              <div className={`mt-3.5 flex flex-wrap gap-2 transition-opacity group-data-[typing]/assistant:mt-0 ${loading || call.active ? "opacity-50" : ""}`} role="group" aria-label={t("اقتراحات سريعة", "Quick suggestions")}>
                {suggestions.map((text) => (
                  <motion.button
                    key={text}
                    variants={fadeUp}
                    whileTap={{ scale: 0.95 }}
                    type="button"
                    onClick={() => void handleSendMessage(text)}
                    disabled={loading || call.active}
                    className="rounded-full border border-brand-line bg-brand-soft px-3.5 py-2 text-[0.8125rem] font-semibold text-brand-soft-ink transition-colors hover:bg-brand hover:text-on-brand disabled:opacity-50"
                  >
                    {text}
                  </motion.button>
                ))}
              </div>
            </div>
          </motion.section>
        )}

        {messages.map((msg, index) => {
          const previous = messages[index - 1];
          const firstOfGroup = !previous || previous.sender !== msg.sender;
          const lastOfGroup = index === messages.length - 1 || messages[index + 1]?.sender !== msg.sender;
          const isUser = msg.sender === "user";
          const isError = msg.sender === "error";
          if (msg.sender === "notice") {
            return (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={spring.smooth}
                className="flex justify-center pt-1"
              >
                <div className="flex max-w-[92%] flex-col items-center gap-2 rounded-2xl border border-line bg-surface-2 px-4 py-3 text-center">
                  <p className="flex items-start gap-2 text-[0.8125rem] leading-relaxed text-ink-2">
                    {msg.callAgain ? <PhoneOff className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" /> : <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden="true" />}
                    <span dir="auto">{msg.text}</span>
                  </p>
                  {msg.callAgain && index === messages.length - 1 && !call.active && (
                    <button type="button" onClick={startCall} disabled={loading} className="btn btn-soft btn-sm">
                      <AudioLines className="size-4" aria-hidden="true" />
                      {t("اتصل مرة ثانية", "Call again")}
                    </button>
                  )}
                </div>
              </motion.div>
            );
          }
          return (
            <motion.div
              key={msg.id}
              initial={msg.id.startsWith("hist-") ? false : { opacity: 0, y: 14, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={spring.smooth}
              className={`flex gap-2 ${isUser ? "justify-end" : isError ? "justify-center" : "justify-start"} ${firstOfGroup ? "pt-1" : "-mt-1.5"}`}
            >
              {!isUser && !isError && (
                <span className="w-7 shrink-0 self-end">
                  {lastOfGroup && (
                    <Image src="/brand/jubran-emblem.webp" alt="" width={56} height={56} className="size-7 rounded-full bg-brand object-cover" />
                  )}
                </span>
              )}
              <div className={`flex max-w-[84%] flex-col ${isUser ? "items-end" : isError ? "items-center" : "items-start"}`}>
                <div
                  className={`whitespace-pre-wrap text-[0.9375rem] leading-relaxed ${
                    isError
                      ? "flex items-start gap-2 rounded-2xl border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-sm text-danger-ink"
                      : isUser
                        ? "rounded-[1.25rem] rounded-ee-md bg-brand px-4 py-2.5 text-on-brand shadow-glow"
                        : "rounded-[1.25rem] rounded-es-md border border-line bg-surface px-4 py-2.5 text-ink shadow-card"
                  }`}
                >
                  {isError && <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
                  {/* Each message reads in its own direction (an English question in the Arabic chat). */}
                  <span dir="auto">{msg.text}</span>

                  {/* The order summary, with its confirm button */}
                  {msg.action?.type === "AWAITING_ORDER_CONFIRMATION" && msg.draft && msg.draft.items && msg.draft.items.length > 0 && (
                    <div className="mt-3 overflow-hidden rounded-2xl border border-line bg-surface-2 text-ink">
                      <div className="flex items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
                        <span className="flex items-center gap-1.5 text-[0.8125rem] font-bold">
                          <ShoppingBag className="size-4 text-brand" aria-hidden="true" />
                          {t("ملخص طلبك", "Your order")}
                        </span>
                        <span className="text-[0.9375rem] font-bold tabular-nums text-brand">{inLang(msg.draft.total_display_ar, msg.draft.total_display_en)}</span>
                      </div>
                      <ul className="space-y-1.5 px-3.5 py-2.5">
                        {msg.draft.items.map((it, i) => (
                          <li key={i} className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
                            <span className="text-ink-2"><span className="font-bold tabular-nums text-ink">{it.quantity}×</span> {inLang(it.name_ar, it.name_en)}</span>
                            <span className="shrink-0 tabular-nums text-muted">{inLang(it.line_total_display_ar, it.line_total_display_en)}</span>
                          </li>
                        ))}
                      </ul>
                      {msg.id === activeSummaryId ? (
                        <div className="flex gap-2 border-t border-line p-2.5">
                          <button
                            type="button"
                            onClick={() => void handleConfirmSummary()}
                            disabled={confirming || loading}
                            className="btn btn-primary btn-sm flex-1"
                          >
                            {confirming ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Check className="size-4" aria-hidden="true" />}
                            {confirming ? t("جاري الإرسال…", "Sending…") : t("تأكيد وإرسال الطلب", "Confirm & send order")}
                          </button>
                          <Link href="/orders" onClick={onClose} className="btn btn-secondary btn-sm">
                            {t("تعديل", "Edit")}
                          </Link>
                        </div>
                      ) : (
                        <p className="border-t border-line px-3.5 py-2 text-xs text-subtle">{t("ملخص سابق", "Earlier summary")}</p>
                      )}
                    </div>
                  )}

                  {/* A change to an order already sent, waiting for the guest's yes */}
                  {msg.action?.type === "AWAITING_AMENDMENT_CONFIRMATION" && msg.action.amendment && (
                    <div className="mt-3 overflow-hidden rounded-2xl border border-line bg-surface-2 text-ink">
                      <div className="flex items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
                        <span className="flex items-center gap-1.5 text-[0.8125rem] font-bold">
                          <Pencil className="size-4 text-info" aria-hidden="true" />
                          {t(`تعديل الطلب ${msg.action.amendment.order_number}`, `Change to order ${msg.action.amendment.order_number}`)}
                        </span>
                        <span className="text-[0.9375rem] font-bold tabular-nums text-brand">{inLang(msg.action.amendment.total_after_display_ar, msg.action.amendment.total_after_display_en)}</span>
                      </div>
                      <ul className="space-y-1 px-3.5 py-2.5">
                        {msg.action.amendment.changes.map((change, i) => (
                          <li key={i} className={`text-[0.8125rem] ${change.type === "removed" ? "text-danger-ink" : change.type === "added" ? "text-success-ink" : "text-ink-2"}`}>{changeLine(change)}</li>
                        ))}
                      </ul>
                      {msg.action.amendment.kitchen_already_preparing && (
                        <p className="mx-3.5 mb-2.5 rounded-xl bg-warning-soft px-3 py-2 text-xs font-semibold text-warning-ink">{t("المطبخ بدأ بالتحضير، والموظفون سيرون التعديل فوراً.", "The kitchen already started; staff will see the change right away.")}</p>
                      )}
                      {msg.id === activeAmendmentId ? (
                        <div className="border-t border-line p-2.5">
                          <button
                            type="button"
                            onClick={() => void handleConfirmAmendment()}
                            disabled={confirming || loading}
                            className={`btn btn-sm w-full ${msg.action.amendment.cancels_order ? "btn-danger" : "btn-primary"}`}
                          >
                            {confirming && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
                            {confirming ? t("جاري التعديل…", "Updating…") : msg.action.amendment.cancels_order ? t("تأكيد إلغاء الطلب", "Confirm cancelling the order") : t("تأكيد التعديل", "Confirm the change")}
                          </button>
                        </div>
                      ) : (
                        <p className="border-t border-line px-3.5 py-2 text-xs text-subtle">{t("تعديل سابق", "Earlier change")}</p>
                      )}
                    </div>
                  )}

                  {msg.action?.type === "ORDER_AMENDED" && (
                    <div className="mt-2.5 flex items-center gap-1.5 rounded-xl bg-info-soft px-3 py-2 text-xs font-bold text-info-ink">
                      <Pencil className="size-3.5" aria-hidden="true" />
                      <span>{t("تم تعديل الطلب", "Order updated")}</span>
                    </div>
                  )}

                  {msg.action?.type === "ORDER_SUBMITTED" && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ ...spring.smooth, delay: 0.05 }}
                      className="mt-3 overflow-hidden rounded-2xl border border-success/25 bg-surface-2 text-ink"
                    >
                      <div className="flex items-center gap-2.5 px-3.5 py-2.5">
                        <motion.span
                          initial={{ scale: 0, rotate: -30 }}
                          animate={{ scale: 1, rotate: 0 }}
                          transition={{ ...spring.pop, delay: 0.15 }}
                          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-success text-white"
                        >
                          <Check className="size-4" strokeWidth={3} aria-hidden="true" />
                        </motion.span>
                        <div className="min-w-0 leading-tight">
                          <p className="text-[0.8125rem] font-bold">
                            {msg.action.order_number ? t(`طلب ${msg.action.order_number}`, `Order ${msg.action.order_number}`) : t("طلبك", "Your order")}
                          </p>
                          <p className="mt-0.5 text-xs text-muted">{t("أُرسل إلى المطعم", "Sent to the restaurant")}</p>
                        </div>
                      </div>
                      <Link
                        href="/orders#sent-orders"
                        onClick={onClose}
                        className="group flex items-center justify-between gap-2 border-t border-line bg-surface px-3.5 py-3 text-sm font-bold text-brand transition-colors hover:bg-brand-soft"
                      >
                        <span className="flex items-center gap-2">
                          <ReceiptText className="size-4" aria-hidden="true" />
                          {t("تتبع حالة طلبك", "Track your order")}
                        </span>
                        <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5 rtl:-scale-x-100 rtl:group-hover:-translate-x-0.5" aria-hidden="true" />
                      </Link>
                    </motion.div>
                  )}
                </div>
                {msg.timestamp && <span className="mt-1 px-1.5 text-[0.6875rem] text-subtle">{msg.timestamp}</span>}
              </div>
            </motion.div>
          );
        })}

        <AnimatePresence>
          {(loading || (call.active && call.state === "thinking")) && (
            <motion.div
              key="typing"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 6, transition: { duration: 0.15 } }}
              transition={spring.smooth}
              className="flex items-end gap-2"
            >
              <Image src="/brand/jubran-emblem.webp" alt="" width={56} height={56} className="size-7 rounded-full bg-brand object-cover" />
              <div role="status" aria-live="polite" className="flex h-11 items-center rounded-[1.25rem] rounded-es-md border border-line bg-surface px-4 shadow-card">
                <span className="sr-only">{t("المساعد يكتب ردّه…", "The assistant is typing…")}</span>
                <span className="flex items-center gap-1.5" dir="ltr" aria-hidden="true">
                  {[0, 1, 2].map((dot) => (
                    <motion.span
                      key={dot}
                      className="size-2 rounded-full bg-brand/70"
                      animate={{ y: [0, -4, 0], opacity: [0.35, 1, 0.35], scale: [0.85, 1, 0.85] }}
                      transition={{ duration: 1.1, repeat: Infinity, delay: dot * 0.16, ease: "easeInOut" }}
                    />
                  ))}
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

      </div>

      {/* Composer */}
      {/* With the keyboard open it covers the phone's bottom edge, so no extra room is kept there. */}
      <div className="shrink-0 border-t border-line bg-surface/90 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur-xl group-data-[typing]/assistant:pb-2.5 group-data-[typing]/assistant:pt-2.5 sm:px-4">
        {/* During a voice call the composer becomes the call bar, in the same place. */}
        <AnimatePresence mode="wait" initial={false}>
          {call.active ? (
            <VoiceCallBar
              key="call"
              state={call.state}
              muted={call.muted}
              tool={call.tool}
              level={call.level}
              quietLeft={call.quietLeft}
              audioPaused={call.audioPaused}
              onToggleMute={call.toggleMute}
              onEnd={call.end}
              onResumeAudio={call.resumeAudio}
            />
          ) : (
            <motion.form
              key="compose"
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.98 }}
              transition={spring.smooth}
              onSubmit={(e) => {
                e.preventDefault();
                handleSendMessage();
              }}
              className={`flex items-center gap-1.5 rounded-[1.5rem] border bg-canvas p-1.5 transition-[border-color,box-shadow] focus-within:border-brand focus-within:shadow-[0_0_0_4px_var(--ring)] ${isRecording ? "border-danger" : "border-line-strong"}`}
            >
              <button
                type="button"
                onClick={() => void toggleDictation()}
                disabled={transcribing}
                title={isRecording
                  ? t("إيقاف التسجيل وتحويله لنص", "Stop recording and convert to text")
                  : t("إملاء صوتي (تحدث ليتم كتابة النص)", "Voice Dictation (Speech-to-text)")}
                className={`relative flex size-10 shrink-0 items-center justify-center rounded-full transition-colors disabled:opacity-50 ${
                  isRecording ? "bg-danger text-white" : "text-ink-2 hover:bg-surface-3 hover:text-brand"
                }`}
                aria-label={isRecording ? t("إيقاف الإملاء", "Stop dictation") : t("إملاء صوتي", "Voice dictation")}
                aria-pressed={isRecording}
              >
                {isRecording && <span className="absolute inset-0 animate-ring rounded-full bg-danger/60" aria-hidden="true" />}
                {transcribing ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : isRecording ? <Square className="relative size-4 fill-current" aria-hidden="true" /> : <Mic className="size-5" aria-hidden="true" />}
              </button>

              <input
                ref={inputRef}
                type="text"
                placeholder={
                  isRecording
                    ? t("جاري التسجيل... اضغط الزر عند الانتهاء", "Recording... tap the button when done")
                    : transcribing
                      ? t("جاري تحويل الصوت إلى نص...", "Converting speech to text...")
                      : t("اكتب رسالتك أو اطلب أطباقك…", "Type a message or order dishes…")
                }
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                enterKeyHint="send"
                autoComplete="off"
                className="min-w-0 flex-1 bg-transparent px-1.5 py-2 text-base text-ink outline-none placeholder:text-subtle"
                aria-label={t("رسالتك للمساعد", "Your message to the assistant")}
              />

              <AnimatePresence mode="popLayout" initial={false}>
                {inputMessage.trim() ? (
                  <motion.button
                    key="send"
                    type="submit"
                    disabled={loading}
                    // Keeps the typing field (and the phone's keyboard) up after sending.
                    onPointerDown={(event) => event.preventDefault()}
                    initial={{ scale: 0.5, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.5, opacity: 0 }}
                    transition={spring.pop}
                    whileTap={{ scale: 0.9 }}
                    className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand shadow-glow transition-colors hover:bg-brand-hover disabled:opacity-50"
                    aria-label={t("إرسال", "Send")}
                    aria-busy={loading}
                  >
                    {loading ? <ButtonSpinner className="size-[18px]" /> : <SendHorizontal className="size-[18px] rtl:-scale-x-100" aria-hidden="true" />}
                  </motion.button>
                ) : (
                  // Nothing typed: the same button starts a voice call with the waiter, right here.
                  <motion.button
                    key="voice"
                    type="button"
                    onClick={startCall}
                    disabled={loading}
                    initial={{ scale: 0.5, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.5, opacity: 0 }}
                    transition={spring.pop}
                    whileTap={{ scale: 0.9 }}
                    title={t("محادثة صوتية", "Voice conversation")}
                    aria-label={t("ابدأ محادثة صوتية مع النادل", "Start a voice conversation with the waiter")}
                    className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-soft-ink transition-colors hover:bg-brand hover:text-on-brand disabled:opacity-50"
                  >
                    <AudioLines className="size-5" aria-hidden="true" />
                  </motion.button>
                )}
              </AnimatePresence>
            </motion.form>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
