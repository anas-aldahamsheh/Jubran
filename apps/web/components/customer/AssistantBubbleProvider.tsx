"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Image from "next/image";
import { AnimatePresence, motion, useDragControls, type PanInfo, type Variants } from "motion/react";
import { ChevronRight, X } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { AssistantChatPanel } from "@/components/customer/AssistantChatPanel";
import { useMediaQuery, WIDE_QUERY } from "@/lib/useMediaQuery";
import { spring, EASE_IN, EASE_OUT } from "@/lib/motion";

const AssistantBubbleContext = createContext<(() => void) | null>(null);

/** Pages that show the "Hi, I can help" hint next to the bubble (once per visit). */
const HINT_PAGES = ["/menu", "/orders"];
const HINT_SEEN_KEY = "jubran_assistant_hint_seen";

/** iPhone and iPad: the keyboard slides over the page (the browser then pans it) instead of resizing it. */
function keyboardOverlaysPage() {
  return /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.userAgent.includes("Macintosh") && navigator.maxTouchPoints > 1);
}

/** Typing fields: while one has focus on a phone, the chat fills the screen above the keyboard. */
function isTextField(target: EventTarget | null) {
  return target instanceof HTMLTextAreaElement || (target instanceof HTMLInputElement && (target.type === "text" || target.type === "search"));
}

export function useAssistantBubble() {
  const openAssistant = useContext(AssistantBubbleContext);
  if (!openAssistant) throw new Error("AssistantBubbleProvider is missing");
  return openAssistant;
}

/** The assistant's round badge: the Jubran calligraphy on navy. */
function AssistantAvatar({ size = 60 }: { size?: number }) {
  return (
    <span className="relative block" style={{ width: size, height: size }} aria-hidden="true">
      <span className="absolute inset-0 rounded-full bg-gradient-to-br from-[#41579e] via-brand to-[#151e40] shadow-glow ring-[3px] ring-surface" />
      <Image
        src="/brand/jubran-emblem.webp"
        alt=""
        width={192}
        height={192}
        draggable={false}
        className="absolute inset-0 size-full select-none rounded-full"
        priority
      />
      <span className="absolute inset-[3px] rounded-full bg-[radial-gradient(circle_at_30%_25%,rgba(255,255,255,0.25),transparent_55%)]" />
    </span>
  );
}

export function AssistantBubbleProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { t, dir } = useLanguage();
  const wide = useMediaQuery(WIDE_QUERY);
  const dragControls = useDragControls();
  const panelRef = useRef<HTMLElement>(null);
  // The keyboard's height the last time it covered the page (iPhone), to make room for it at once next time.
  const keyboardRef = useRef(0);
  const [openState, setIsOpen] = useState(false);
  const [typing, setTyping] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const [hintVisible, setHintVisible] = useState(false);
  // Never shown on pages without the assistant (sign-in, admin), nor on the table's welcome.
  const showBubble = pathname !== "/login" && !pathname.startsWith("/admin") && !pathname.startsWith("/t/") && pathname !== "/assistant";
  const isOpen = openState && showBubble;
  const typingOnPhone = typing && !wide;

  const openAssistant = useCallback(() => {
    setHasOpened(true);
    setHintVisible(false);
    try {
      sessionStorage.setItem(HINT_SEEN_KEY, "1");
    } catch {
      // Storage unavailable (private mode): the hint may show again, that's all.
    }
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    // The keyboard leaves with the chat.
    const active = document.activeElement;
    if (active instanceof HTMLElement && panelRef.current?.contains(active)) active.blur();
    setIsOpen(false);
  }, []);

  // Another page asked for the assistant to open here (for example /assistant).
  useEffect(() => {
    if (!showBubble || sessionStorage.getItem("open_jubran_assistant") !== "1") return;
    sessionStorage.removeItem("open_jubran_assistant");
    const timer = window.setTimeout(openAssistant, 0);
    return () => window.clearTimeout(timer);
  }, [pathname, showBubble, openAssistant]);

  // A friendly hint the first time a guest browses the menu or orders in this visit.
  useEffect(() => {
    if (!showBubble || hasOpened || !HINT_PAGES.includes(pathname)) return;
    let seen = false;
    try {
      seen = sessionStorage.getItem(HINT_SEEN_KEY) === "1";
    } catch {
      seen = false;
    }
    if (seen) return;
    const show = window.setTimeout(() => setHintVisible(true), 2600);
    const hide = window.setTimeout(() => setHintVisible(false), 11000);
    return () => {
      window.clearTimeout(show);
      window.clearTimeout(hide);
    };
  }, [pathname, showBubble, hasOpened]);

  const dismissHint = () => {
    setHintVisible(false);
    try {
      sessionStorage.setItem(HINT_SEEN_KEY, "1");
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [isOpen, close]);

  // On phones the chat covers the page, so the page behind stays still (iPhone included).
  useEffect(() => {
    if (!isOpen || wide) return;
    const { body, documentElement: html } = document;
    const previous = { html: html.style.overflow, body: body.style.overflow, overscroll: html.style.overscrollBehavior };
    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    html.style.overscrollBehavior = "none";
    return () => {
      html.style.overflow = previous.html;
      body.style.overflow = previous.body;
      html.style.overscrollBehavior = previous.overscroll;
    };
  }, [isOpen, wide]);

  // The chat sits on the part of the screen the guest can actually see. Where the keyboard
  // resizes the page (Android) the screen height alone does it, in the same frame. Where it
  // slides over the page (iPhone, iPad) the visible part is followed here, written straight
  // to the element, so nothing lags behind the keyboard or hides under it.
  useEffect(() => {
    const panel = panelRef.current;
    const viewport = window.visualViewport;
    if (!panel || !viewport) return;
    let frame = 0;
    const place = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const covered = window.innerHeight - viewport.height;
        if (covered > 80 || viewport.offsetTop > 0) {
          if (covered > 150 && !wide) keyboardRef.current = covered;
          panel.style.setProperty("--vv-top", `${viewport.offsetTop}px`);
          panel.style.setProperty("--vv-height", `${viewport.height}px`);
          panel.style.setProperty("--vv-bottom", `${Math.max(0, window.innerHeight - viewport.offsetTop - viewport.height)}px`);
        } else {
          for (const name of ["--vv-top", "--vv-height", "--vv-bottom"]) panel.style.removeProperty(name);
        }
      });
    };
    place();
    viewport.addEventListener("resize", place);
    viewport.addEventListener("scroll", place);
    return () => {
      cancelAnimationFrame(frame);
      viewport.removeEventListener("resize", place);
      viewport.removeEventListener("scroll", place);
      for (const name of ["--vv-top", "--vv-height", "--vv-bottom"]) panel.style.removeProperty(name);
    };
  }, [wide, hasOpened]);

  // Tablets and computers: a click anywhere outside the chat closes it, like the X.
  useEffect(() => {
    if (!isOpen || !wide) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element) || target === document.documentElement) return; // the page's scrollbar
      if (panelRef.current?.contains(target)) return;
      // Confirmations opened from the chat, and the page's own "ask the assistant" buttons, keep it open.
      if (target.closest('[role="dialog"], [role="alertdialog"], [data-assistant-trigger]')) return;
      close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [isOpen, wide, close]);

  // iPhone: the moment the typing field gets focus, the chat already ends where the keyboard
  // will, so the browser has no reason to push the page up (that push was the jump). The
  // first time uses a close estimate; after that the keyboard's measured height.
  const makeRoomForKeyboard = () => {
    const panel = panelRef.current;
    const viewport = window.visualViewport;
    if (wide || !panel || !viewport || !keyboardOverlaysPage()) return;
    if (window.innerHeight - viewport.height > 150) return; // already open
    const keyboard = keyboardRef.current || Math.round(window.screen.height * 0.4);
    panel.style.setProperty("--vv-top", "0px");
    panel.style.setProperty("--vv-height", `${window.innerHeight - keyboard}px`);
    // No on-screen keyboard came (a hardware keyboard, for example): take the full height back.
    window.setTimeout(() => {
      if (window.innerHeight - viewport.height <= 150 && viewport.offsetTop === 0) giveBackKeyboardRoom();
    }, 700);
  };
  // And the room comes back as the keyboard starts to leave, not after it has gone.
  const giveBackKeyboardRoom = () => {
    const panel = panelRef.current;
    if (wide || !panel || !keyboardOverlaysPage()) return;
    panel.style.removeProperty("--vv-top");
    panel.style.removeProperty("--vv-height");
  };

  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.y > 120 || info.velocity.y > 700) close();
  };

  const panelVariants: Variants = wide
    ? {
        open: { opacity: 1, scale: 1, y: 0, visibility: "visible" as const, transition: spring.sheet },
        closed: { opacity: 0, scale: 0.9, y: 24, transition: { duration: 0.2, ease: EASE_IN }, transitionEnd: { visibility: "hidden" as const } },
      }
    : {
        open: { y: 0, visibility: "visible" as const, transition: spring.sheet },
        closed: { y: "105%", transition: { duration: 0.26, ease: EASE_IN }, transitionEnd: { visibility: "hidden" as const } },
      };

  return (
    <AssistantBubbleContext.Provider value={openAssistant}>
      {children}
      {showBubble && (
        <div dir={dir}>
          {/* Phones: the page dims behind the chat. */}
          <AnimatePresence>
            {isOpen && !wide && (
              <motion.div
                key="assistant-backdrop"
                aria-hidden="true"
                onClick={close}
                className="fixed inset-0 z-[70] bg-[var(--overlay)] backdrop-blur-[2px]"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25, ease: EASE_OUT }}
              />
            )}
          </AnimatePresence>

          {hasOpened && (
            <motion.aside
              ref={panelRef}
              role="dialog"
              aria-modal={!wide}
              aria-label={t("محادثة مساعد جبران", "Jubran assistant chat")}
              aria-hidden={!isOpen}
              inert={!isOpen}
              initial="closed"
              animate={isOpen ? "open" : "closed"}
              variants={panelVariants}
              drag={wide ? false : "y"}
              dragListener={false}
              dragControls={dragControls}
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.6 }}
              onDragEnd={onDragEnd}
              data-typing={typingOnPhone || undefined}
              onFocus={(event) => {
                if (!isTextField(event.target)) return;
                makeRoomForKeyboard();
                setTyping(true);
              }}
              onBlur={(event) => {
                if (!isTextField(event.target)) return;
                giveBackKeyboardRoom();
                setTyping(false);
              }}
              // The visible part of the screen (kept by the effect above). Phones: all of it less a
              // small gap at the top, and all of it while typing. Larger screens: a card in the
              // corner, above the keyboard when one is open.
              style={wide
                ? { bottom: "calc(var(--vv-bottom, 0px) + 1.5rem)", height: "min(46rem, calc(var(--vv-height, 100dvh) - 3rem))" }
                : { top: "calc(var(--vv-top, 0px) + var(--sheet-gap))", height: "calc(var(--vv-height, 100dvh) - var(--sheet-gap))" }}
              className={`group/assistant fixed z-[80] flex flex-col overflow-hidden border border-line bg-canvas shadow-float ${wide
                ? "end-6 w-[26.5rem] origin-bottom-right rounded-[1.75rem] rtl:origin-bottom-left"
                : "inset-x-0 rounded-t-[1.75rem] [--sheet-gap:max(0.5rem,env(safe-area-inset-top))] data-[typing]:rounded-none data-[typing]:border-0 data-[typing]:[--sheet-gap:env(safe-area-inset-top)]"}`}
            >
              {!wide && !typingOnPhone && (
                <div
                  className="absolute inset-x-0 top-0 z-10 flex h-5 cursor-grab touch-none justify-center pt-2 active:cursor-grabbing"
                  onPointerDown={(event) => dragControls.start(event)}
                  aria-hidden="true"
                >
                  <span className="h-1.5 w-10 rounded-full bg-line-strong" />
                </div>
              )}
              <AssistantChatPanel open={isOpen} onClose={close} onHeaderPointerDown={wide ? undefined : (event) => dragControls.start(event)} />
            </motion.aside>
          )}

          {/* The bubble */}
          <AnimatePresence>
            {!isOpen && (
              <motion.div
                key="assistant-launcher"
                className="fixed bottom-[max(0.75rem,env(safe-area-inset-bottom))] end-3 z-[60] flex items-end gap-2 sm:bottom-6 sm:end-6"
                initial={{ opacity: 0, scale: 0.6, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.6, y: 10, transition: { duration: 0.15 } }}
                transition={{ ...spring.pop, delay: 0.1 }}
              >
                <AnimatePresence>
                  {hintVisible && (
                    <motion.div
                      key="assistant-hint"
                      initial={{ opacity: 0, x: dir === "rtl" ? -12 : 12, scale: 0.92 }}
                      animate={{ opacity: 1, x: 0, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.92, transition: { duration: 0.15 } }}
                      transition={spring.smooth}
                      className="relative hidden w-[min(16rem,calc(100vw-6.5rem))] rounded-2xl border border-line bg-elevated text-ink shadow-float min-[400px]:block"
                    >
                      <button
                        type="button"
                        onClick={openAssistant}
                        className="block w-full rounded-2xl p-4 pe-10 text-start"
                      >
                        <span className="flex items-center gap-2 text-[0.9375rem] font-bold leading-tight">
                          <span className="size-2 shrink-0 rounded-full bg-success" aria-hidden="true" />
                          {t("أهلاً وسهلاً", "Hi there")}
                          <span aria-hidden="true">👋</span>
                        </span>
                        <span className="mt-1.5 block text-pretty text-sm leading-relaxed text-muted">
                          {t("أنا نادلك الذكي، بساعدك تختار وتطلب.", "I'm your smart waiter. I'll help you choose and order.")}
                        </span>
                        <span className="mt-3 inline-flex items-center gap-1 rounded-full border border-brand-line bg-brand-soft px-3 py-1 text-xs font-bold text-brand-soft-ink">
                          {t("اسألني الآن", "Ask me now")}
                          <ChevronRight className="size-3.5 rtl:-scale-x-100" aria-hidden="true" />
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={dismissHint}
                        aria-label={t("إخفاء", "Dismiss")}
                        className="absolute end-2.5 top-2.5 flex size-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-3 hover:text-ink"
                      >
                        <X className="size-4" aria-hidden="true" />
                      </button>
                      {/* A small tail pointing at the round button. */}
                      <span
                        className="absolute -end-[7px] bottom-6 size-3 rotate-45 border-e border-t border-line bg-elevated rtl:-rotate-45 sm:bottom-[26px]"
                        aria-hidden="true"
                      />
                    </motion.div>
                  )}
                </AnimatePresence>

                <motion.button
                  type="button"
                  onClick={openAssistant}
                  aria-expanded={isOpen}
                  aria-label={t("افتح مساعد جبران", "Open Jubran assistant")}
                  title={t("مساعد جبران", "Jubran Assistant")}
                  whileHover={{ scale: 1.06, y: -2 }}
                  whileTap={{ scale: 0.92 }}
                  transition={spring.snappy}
                  className="group relative rounded-full focus-visible:outline-offset-4"
                >
                  <span className="absolute inset-0 animate-ring rounded-full bg-brand/40" aria-hidden="true" />
                  <AssistantAvatar size={wide ? 64 : 60} />
                  <span className="absolute -top-0.5 end-0.5 flex size-4 items-center justify-center rounded-full bg-surface" aria-hidden="true">
                    <span className="size-2.5 rounded-full bg-success" />
                  </span>
                </motion.button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </AssistantBubbleContext.Provider>
  );
}
