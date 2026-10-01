"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  BellRing, Check, ChefHat, Clock3, ConciergeBell, Hand, Layers, LoaderCircle,
  MessageSquareWarning, Pencil, ReceiptText, Send, SprayCan, Star, Trash2,
} from "lucide-react";
import { CurrentOrder } from "@/components/customer/CurrentOrder";
import { AskAssistantButton } from "@/components/customer/AskAssistantButton";
import { apiFetch, ApiException, getCustomerSessionContext } from "@/lib/api";
import { hasVisitEnded, useVisitSignals } from "@/lib/visitStatus";
import { useLanguage } from "@/context/LanguageContext";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { useAssistantBubble } from "@/components/customer/AssistantBubbleProvider";
import { restaurantTime } from "@/lib/time";
import { useGuestBasketCount } from "@/components/customer/GuestShell";
import { SentOrdersSkeleton } from "@/components/customer/GuestSkeletons";
import { Sheet, SheetBody, SheetFooter, SheetHeader } from "@/components/ui/Sheet";
import { QuantityStepper } from "@/components/ui/QuantityStepper";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import { ButtonSpinner, LoadError, Reveal, SlowNote } from "@/components/ui/Feedback";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { Toast } from "@/components/ui/Toast";
import { formatTableNumber } from "@/lib/useTableNumber";
import { EASE_OUT, spring } from "@/lib/motion";

interface OrderItem {
  item_id?: string;
  added_later?: boolean;
  name_ar: string;
  name_en?: string;
  quantity: number;
  unit_price_display: string;
  unit_price_display_en?: string;
  line_total_display: string;
  line_total_display_en?: string;
  note?: string;
}

interface OrderSummary {
  order_id: string;
  order_number: string;
  version?: number;
  /** This guest's own order (only those can be changed). */
  mine?: boolean;
  /** Still changeable: the guest's own order, not ready or served yet. */
  editable?: boolean;
  amended?: boolean;
  cancelled_by_guest?: boolean;
  status: "PENDING_APPROVAL" | "PREPARING" | "READY" | "CLOSED" | "CANCELLED";
  status_display_ar: string;
  status_display_en?: string;
  is_served: boolean;
  total_display_ar: string;
  total_display_en?: string;
  items: OrderItem[];
  created_at: string;
  cancelled_with_visit?: boolean;
}

interface ServiceRequestSummary {
  request_id: string;
  type: "STAFF" | "TISSUES" | "CLEAN_TABLE" | "BILL";
  status: "OPEN" | "IN_PROGRESS" | "RESOLVED" | "CANCELLED";
  created_at: string;
  can_cancel: boolean;
  cancelled_with_visit?: boolean;
}

export default function CustomerOrdersPage() {
  const { lang, dir, t } = useLanguage();
  const { showAlert, showConfirm } = useGlobalDialog();
  // The page language, falling back to Arabic when a field has no English version.
  const inLang = (ar: string, en?: string) => (lang === "en" && en ? en : ar);
  const openAssistant = useAssistantBubble();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [serviceRequests, setServiceRequests] = useState<ServiceRequestSummary[]>([]);
  const ordersFetchInFlightRef = useRef(false);
  const [tableNumber, setTableNumber] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // The table service being requested right now (its button shows it is on its way).
  const [sendingService, setSendingService] = useState<string | null>(null);
  const requestSending = sendingService !== null;
  const [cancellingRequestId, setCancellingRequestId] = useState<string | null>(null);
  const [requestFeedback, setRequestFeedback] = useState<string | null>(null);
  const [showComplaintModal, setShowComplaintModal] = useState(false);
  const [complaintMessage, setComplaintMessage] = useState("");
  const [complaintSending, setComplaintSending] = useState(false);
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  // No stars pre-selected: the guest chooses, so ratings reflect real opinions.
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackSending, setFeedbackSending] = useState(false);
  const [draftCount, setDraftCount] = useState(0);
  const [amendingOrderId, setAmendingOrderId] = useState<string | null>(null);
  const [amendingItemId, setAmendingItemId] = useState<string | null>(null);
  // Failed order loads in a row (retried by itself; see useAutoRetry).
  const [ordersFailures, setOrdersFailures] = useState(0);
  const sentRef = useRef<HTMLElement>(null);
  const [flashSent, setFlashSent] = useState(false);

  const fetchOrders = async (showInitialLoading = false) => {
    if (ordersFetchInFlightRef.current) return;
    ordersFetchInFlightRef.current = true;
    if (showInitialLoading) setLoading(true);
    try {
      const data = await apiFetch<OrderSummary[]>("/orders");
      setOrders(data);
      setOrdersFailures(0);
    } catch (err) {
      // A closed visit is explained to the guest by VisitStatus; nothing to log.
      if (!(err instanceof ApiException && err.status === 401)) {
        console.error("Failed to load orders:", err);
        setOrdersFailures((count) => count + 1);
      }
    } finally {
      if (showInitialLoading) setLoading(false);
      ordersFetchInFlightRef.current = false;
    }
  };

  const fetchServiceRequests = async () => {
    try {
      const data = await apiFetch<ServiceRequestSummary[]>("/service-requests");
      setServiceRequests(data);
    } catch (err) {
      if (!(err instanceof ApiException && err.status === 401)) console.error("Failed to load service requests:", err);
    }
  };

  useEffect(() => {
    void Promise.resolve().then(() => {
      void fetchOrders(true);
      void fetchServiceRequests();
    });
    // Live updates arrive over the table's socket; this slow poll is only a safety net.
    const interval = setInterval(() => {
      if (hasVisitEnded()) return;
      void fetchOrders();
      void fetchServiceRequests();
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    getCustomerSessionContext()
      .then((context) => setTableNumber(context?.table_number ?? null))
      .catch(() => setTableNumber(null));
  }, []);

  useAutoRetry(ordersFailures, () => {
    void fetchOrders(true);
    void fetchServiceRequests();
  });
  useGuestBasketCount(draftCount);

  // "Track your order" in the chat opens /orders#sent-orders: once the orders are in, go to them.
  useEffect(() => {
    if (loading) return;
    let unflash = 0;
    const focusSent = () => {
      if (window.location.hash !== "#sent-orders" || !sentRef.current) return;
      sentRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      setFlashSent(true);
      window.clearTimeout(unflash);
      unflash = window.setTimeout(() => setFlashSent(false), 1800);
    };
    const timer = window.setTimeout(focusSent, 150);
    window.addEventListener("hashchange", focusSent);
    return () => {
      window.clearTimeout(timer);
      window.clearTimeout(unflash);
      window.removeEventListener("hashchange", focusSent);
    };
  }, [loading]);

  // The kitchen or staff changed something for this table: reload it now.
  useVisitSignals((message) => {
    if (message.type === "orders.changed" || message.type === "reconnected") void fetchOrders();
    if (message.type === "service_requests.changed" || message.type === "reconnected") void fetchServiceRequests();
  });

  const handleQuickService = async (type: string, label: string) => {
    try {
      setSendingService(type);
      const result = await apiFetch<{ is_duplicate?: boolean }>("/service-requests", {
        method: "POST",
        body: JSON.stringify({ type }),
      });
      await fetchServiceRequests();
      setRequestFeedback(result.is_duplicate
        ? t(`طلب "${label}" قيد المتابعة بالفعل.`, `The "${label}" request is already in progress.`)
        : t(`تم إرسال طلب "${label}" لطاقم الخدمة بنجاح.`, `Request "${label}" sent to staff.`));
      setTimeout(() => setRequestFeedback(null), 3000);

      // Trigger optional non-blocking feedback modal upon bill request (REQ-018)
      if (type === "BILL" && !feedbackSubmitted) {
        setShowFeedbackModal(true);
      }
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        void showAlert(err.message);
      }
    } finally {
      setSendingService(null);
    }
  };

  const handleSubmitComplaint = async () => {
    const message = complaintMessage.trim();
    if (message.length < 3) {
      void showAlert(t("اكتب تفاصيل الشكوى (3 أحرف على الأقل).", "Please describe the complaint using at least 3 characters."));
      return;
    }
    try {
      setComplaintSending(true);
      await apiFetch("/complaints", {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      setShowComplaintModal(false);
      setComplaintMessage("");
      setRequestFeedback(t("تم إرسال الشكوى إلى الإدارة بنجاح.", "Your complaint was sent to the restaurant."));
      setTimeout(() => setRequestFeedback(null), 4000);
    } catch (err: unknown) {
      if (err instanceof ApiException) void showAlert(err.message);
    } finally {
      setComplaintSending(false);
    }
  };

  const handleCancelServiceRequest = async (request: ServiceRequestSummary) => {
    const label = {
      STAFF: t("طلب الموظف", "staff request"),
      TISSUES: t("طلب المناديل", "tissues request"),
      CLEAN_TABLE: t("طلب تنظيف الطاولة", "table cleaning request"),
      BILL: t("طلب الحساب", "bill request"),
    }[request.type];
    if (!await showConfirm(t(`هل تريد إلغاء ${label}؟`, `Cancel the ${label}?`), {
      title: t("إلغاء طلب الخدمة", "Cancel service request"),
      tone: "danger",
      confirmLabel: t("إلغاء الطلب", "Cancel request"),
    })) return;

    try {
      setCancellingRequestId(request.request_id);
      await apiFetch(`/service-requests/${request.request_id}/cancel`, { method: "POST" });
      await fetchServiceRequests();
      setRequestFeedback(t("تم إلغاء الطلب، وسيظهر للإدارة أنه أُرسل ثم أُلغي.", "Request cancelled. The restaurant will see that it was sent and then cancelled."));
      setTimeout(() => setRequestFeedback(null), 3500);
    } catch (err: unknown) {
      if (err instanceof ApiException) void showAlert(err.message);
    } finally {
      setCancellingRequestId(null);
    }
  };

  // The guest changes an order already sent (until it is ready): fewer, more, or remove a dish.
  const handleAmendItem = async (order: OrderSummary, item: OrderItem, quantity: number) => {
    if (!item.item_id) return;
    const removingAll = quantity === 0 && order.items.length === 1;
    if (removingAll && !await showConfirm(
      t("هذا آخر صنف بالطلب، فحذفه يلغي الطلب كله. هل تريد إلغاء الطلب؟", "This is the last dish, so removing it cancels the whole order. Cancel the order?"),
      { title: t("إلغاء الطلب", "Cancel order"), tone: "danger", confirmLabel: t("إلغاء الطلب", "Cancel order") },
    )) return;
    if (!removingAll && order.status === "PREPARING" && !await showConfirm(
      t("المطبخ بدأ بتحضير هذا الطلب. سيصل تعديلك للموظفين فوراً. هل تريد المتابعة؟", "The kitchen has started this order. Your change will reach the staff right away. Continue?"),
      { title: t("تعديل طلب قيد التحضير", "Change an order being prepared"), confirmLabel: t("تعديل", "Change") },
    )) return;
    try {
      setAmendingOrderId(order.order_id);
      setAmendingItemId(item.item_id);
      await apiFetch(`/orders/${order.order_id}/amend`, {
        method: "POST",
        body: JSON.stringify({
          operations: [quantity === 0 ? { op: "remove", item_id: item.item_id } : { op: "set_quantity", item_id: item.item_id, quantity }],
          expected_version: order.version,
        }),
      });
      setRequestFeedback(removingAll
        ? t(`تم إلغاء الطلب ${order.order_number}.`, `Order ${order.order_number} was cancelled.`)
        : t(`تم تعديل الطلب ${order.order_number}.`, `Order ${order.order_number} was updated.`));
      setTimeout(() => setRequestFeedback(null), 3000);
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        const english = typeof err.details?.message_en === "string" ? err.details.message_en : null;
        void showAlert(lang === "en" && english ? english : err.message);
      }
    } finally {
      // The line keeps showing its change is on its way until the refreshed order is in.
      await fetchOrders();
      setAmendingOrderId(null);
      setAmendingItemId(null);
    }
  };

  const handleSendFeedback = async () => {
    if (rating < 1 || feedbackSending) return;
    try {
      setFeedbackSending(true);
      await apiFetch("/feedback", {
        method: "POST",
        body: JSON.stringify({ rating, comment: comment.trim() || null }),
      });
      setFeedbackSubmitted(true);
      setShowFeedbackModal(false);
      setRequestFeedback(t("شكراً لتقييمك ومشاركتنا رأيك 💚", "Thank you for your rating and feedback! 💚"));
      setTimeout(() => setRequestFeedback(null), 3500);
    } catch (err: unknown) {
      if (err instanceof ApiException) void showAlert(err.message);
    } finally {
      setFeedbackSending(false);
    }
  };

  // Exactly three states for guests: awaiting confirmation, preparing, ready.
  // Being served at the table does not add a fourth state (the order stays "ready").
  const getStatusBadge = (order: OrderSummary) => {
    if (order.status === "CANCELLED") {
      return { bg: "bg-stone-50 border-stone-200 text-stone-800", dot: "bg-stone-500", label: t("أُلغي الطلب", "Order cancelled") };
    }
    if (order.status === "CLOSED") {
      return { bg: "bg-amber-50 border-amber-200 text-amber-900", dot: "bg-amber-600", label: t("أُغلق الطلب", "Order closed") };
    }
    switch (order.status) {
      case "PENDING_APPROVAL":
        return {
          bg: "bg-amber-50 border-amber-200 text-amber-800",
          dot: "bg-amber-500",
          label: t("بانتظار التأكيد", "Awaiting Confirmation"),
        };
      case "PREPARING":
        return {
          bg: "bg-orange-50 border-orange-200 text-orange-800",
          dot: "bg-orange-500 animate-pulse",
          label: t("قيد التحضير في المطبخ", "In the Kitchen"),
        };
      case "READY":
        return {
          bg: "bg-green-50 border-green-200 text-green-800",
          dot: "bg-green-600",
          label: t("جاهز", "Ready"),
        };
      default:
        return {
          bg: "bg-gray-50 border-gray-200 text-gray-800",
          dot: "bg-gray-400",
          label: order.status,
        };
    }
  };

  const statusTone = (order: OrderSummary): { tone: PillTone; pulse: boolean } => {
    if (order.status === "CANCELLED") return { tone: "neutral", pulse: false };
    if (order.status === "CLOSED") return { tone: "warning", pulse: false };
    if (order.status === "PENDING_APPROVAL") return { tone: "warning", pulse: true };
    if (order.status === "PREPARING") return { tone: "preparing", pulse: true };
    if (order.status === "READY") return { tone: "success", pulse: false };
    return { tone: "neutral", pulse: false };
  };
  // The guest sees three steps; being served at the table keeps the order on "ready".
  const orderStep = (order: OrderSummary): number | null =>
    order.status === "PENDING_APPROVAL" ? 0 : order.status === "PREPARING" ? 1 : order.status === "READY" ? 2 : null;
  const steps = [
    { icon: Clock3, label: t("استلمنا طلبك", "Received") },
    { icon: ChefHat, label: t("في المطبخ", "In the kitchen") },
    { icon: BellRing, label: t("جاهز", "Ready") },
  ];
  const serviceItems = [
    { type: "STAFF", label: t("طلب موظف", "Call staff"), icon: Hand },
    { type: "TISSUES", label: t("مناديل إضافية", "Tissues"), icon: Layers },
    { type: "CLEAN_TABLE", label: t("تنظيف الطاولة", "Clean table"), icon: SprayCan },
    { type: "BILL", label: t("طلب الحساب", "Request bill"), icon: ReceiptText },
  ];
  const serviceInfoOf = (type: ServiceRequestSummary["type"]) => ({
    STAFF: { ar: "طلب موظف", en: "Staff assistance", icon: Hand },
    TISSUES: { ar: "مناديل إضافية", en: "Extra tissues", icon: Layers },
    CLEAN_TABLE: { ar: "تنظيف الطاولة", en: "Table cleaning", icon: SprayCan },
    BILL: { ar: "طلب الحساب", en: "Request the bill", icon: ReceiptText },
  }[type]);
  const timeOf = (iso: string) => restaurantTime(iso, lang === "ar" ? "ar-JO" : "en-US", { hour: "2-digit", minute: "2-digit" });
  const hasSent = orders.length > 0 || serviceRequests.length > 0;
  // First load only: later refreshes keep the orders on screen.
  const ordersFailed = ordersFailures > 0 && !hasSent;
  const ordersFirstLoad = loading && !hasSent && !ordersFailed;
  // A basket waiting to be sent comes first; once everything went out, the sent orders
  // lead and the empty basket becomes a slim "order more" line under them.
  const sentFirst = draftCount === 0 && hasSent;

  return (
    <div className="flex flex-1 flex-col overflow-x-clip bg-canvas pb-28 text-ink md:pb-16" dir={dir}>

      {/* Page heading */}
      <section className="relative isolate -mt-16 overflow-hidden md:-mt-[4.5rem]" aria-labelledby="orders-title">
        <div className="absolute inset-0 -z-20 bg-[url('/backdrops/rooftop.webp')] bg-cover bg-[center_40%] dark:brightness-[0.6]" aria-hidden="true" />
        <div className="absolute inset-0 -z-10 bg-gradient-to-b from-canvas/40 via-canvas/80 to-canvas dark:from-canvas/45" aria-hidden="true" />
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 pb-8 pt-24 sm:flex-row sm:items-end sm:justify-between sm:px-6 md:pt-32 lg:px-8">
          <div className="animate-rise [--rise-from:16px]">
            <AnimatePresence mode="wait" initial={false}>
              <motion.p
                key={tableNumber ?? "visit"}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.25, ease: EASE_OUT }}
                className="text-sm font-semibold text-accent-ink"
              >
                {tableNumber ? t(`طاولة ${formatTableNumber(tableNumber)}`, `Table ${formatTableNumber(tableNumber)}`) : t("زيارتك", "Your visit")}
              </motion.p>
            </AnimatePresence>
            <h1 id="orders-title" className="mt-1 font-display text-4xl font-bold leading-tight text-ink sm:text-5xl">{t("طلباتي", "My orders")}</h1>
            <p className="mt-3 max-w-lg text-[0.9375rem] leading-relaxed text-ink-2">{t("راجع سلتك قبل الإرسال، وتابع حالة الطلبات التي أرسلتها للمطبخ.", "Review your basket before sending it, and follow the orders you sent to the kitchen.")}</p>
          </div>
          <AskAssistantButton label={t("اسأل مساعد جبران", "Ask the Jubran assistant")} onClick={openAssistant} delay={0.1} className="self-start sm:self-auto" />
        </div>
      </section>

      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-8 px-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:px-8">
        <div className="flex min-w-0 flex-col gap-10">
          <motion.div layout="position" transition={spring.smooth} className={sentFirst ? "order-2" : "order-1"}>
            <CurrentOrder onSubmitted={fetchOrders} onDraftCountChange={setDraftCount} hasSentOrders={hasSent} />
          </motion.div>

          {/* Nothing sent and nothing in the basket: the basket's own message says it all. */}
          {(loading || hasSent || draftCount > 0 || ordersFailed) && (
          <motion.section
            layout="position"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={spring.smooth}
            ref={sentRef}
            id="sent-orders"
            aria-labelledby="submitted-orders-title"
            className={`-m-2 scroll-mt-24 rounded-[2rem] p-2 transition-shadow duration-700 ${sentFirst ? "order-1" : "order-2"} ${flashSent ? "shadow-[0_0_0_4px_var(--ring)]" : "shadow-none"}`}
          >
            <div className="mb-4 flex items-end justify-between gap-3">
              <div className="min-w-0">
                <h2 id="submitted-orders-title" className="font-display text-xl font-bold leading-tight text-ink sm:text-2xl">
                  {t("الطلبات المرسلة", "Sent orders")}
                </h2>
                <p className="mt-0.5 text-sm text-muted">{t("وصلت للمطعم، وتتابع حالتها هنا لحظة بلحظة.", "They reached the restaurant: follow them here, live.")}</p>
              </div>
              {orders.length > 0 && <span className="shrink-0 rounded-full bg-surface-3 px-3 py-1 text-xs font-bold tabular-nums text-ink-2">{orders.length}</span>}
            </div>

            <Reveal
              ready={!ordersFirstLoad}
              label={t("جاري تحميل الطلبات...", "Loading orders...")}
              skeleton={<><SentOrdersSkeleton /><SlowNote className="mt-5" /></>}
            >
            {ordersFailed ? (
              <LoadError
                title={t("ما قدرنا نحمّل طلباتك", "We couldn't load your orders")}
                onRetry={() => {
                  void fetchOrders(true);
                  void fetchServiceRequests();
                }}
                retrying={loading}
              />
            ) : orders.length === 0 && serviceRequests.length === 0 ? (
              <p className="flex items-center gap-3 rounded-3xl border border-dashed border-line-strong bg-surface-2/60 px-4 py-4 text-sm text-muted">
                <ReceiptText className="size-5 shrink-0 text-subtle" aria-hidden="true" />
                {t("لما تأكد سلتك يظهر طلبك هنا، وتتابع حالته لحظة بلحظة.", "Once you confirm your basket, your order shows up here with its live status.")}
              </p>
            ) : (
              <div className="space-y-4">
                <AnimatePresence initial={false}>
                  {serviceRequests.map((request) => {
                    const serviceInfo = serviceInfoOf(request.type);
                    const isOpen = request.status === "OPEN";
                    const isInProgress = request.status === "IN_PROGRESS";
                    const isCancelled = request.status === "CANCELLED";
                    const Icon = serviceInfo.icon;
                    return (
                      <motion.article
                        key={request.request_id}
                        layout
                        initial={{ opacity: 0, y: 12 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.97 }}
                        transition={spring.smooth}
                        className="flex items-center justify-between gap-3 rounded-3xl border border-line bg-surface p-4 shadow-card"
                      >
                        <div className="flex min-w-0 items-center gap-3">
                          <span className={`flex size-11 shrink-0 items-center justify-center rounded-2xl ${isCancelled ? "bg-surface-3 text-subtle" : "bg-brand-soft text-brand-soft-ink"}`}>
                            <Icon className="size-5" aria-hidden="true" />
                          </span>
                          <div className="min-w-0">
                            <h3 className="font-bold text-ink">{lang === "ar" ? serviceInfo.ar : serviceInfo.en}</h3>
                            <time className="text-xs text-subtle">{timeOf(request.created_at)}</time>
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-2 sm:flex-row sm:items-center">
                          <StatusPill
                            tone={isOpen ? "warning" : isInProgress ? "preparing" : isCancelled ? "neutral" : "success"}
                            dot={isOpen || isInProgress}
                            pulse={isOpen || isInProgress}
                            size="sm"
                          >
                            {isOpen ? t("بانتظار التأكيد", "Awaiting confirmation") : isInProgress ? t("قيد التحضير", "In progress") : isCancelled ? (request.cancelled_with_visit ? t("أُلغي بسبب إنهاء الزيارة", "Cancelled when the visit ended") : t("أُلغي", "Cancelled")) : t("تمت الخدمة", "Completed")}
                          </StatusPill>
                          {isOpen && request.can_cancel && (
                            <button
                              type="button"
                              onClick={() => void handleCancelServiceRequest(request)}
                              disabled={cancellingRequestId === request.request_id}
                              className="btn btn-danger-soft btn-sm min-h-8 px-3"
                              aria-busy={cancellingRequestId === request.request_id}
                            >
                              {cancellingRequestId === request.request_id && <ButtonSpinner className="size-3.5" />}
                              {cancellingRequestId === request.request_id ? t("جارٍ الإلغاء", "Cancelling") : t("إلغاء", "Cancel")}
                            </button>
                          )}
                        </div>
                      </motion.article>
                    );
                  })}
                </AnimatePresence>

                {orders.map((ord, orderIndex) => {
                  const badge = getStatusBadge(ord);
                  const { tone, pulse } = statusTone(ord);
                  const step = orderStep(ord);
                  return (
                    <motion.article
                      key={ord.order_id}
                      layout
                      initial={{ opacity: 0, y: 16 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ ...spring.smooth, delay: Math.min(orderIndex, 5) * 0.05 }}
                      className={`overflow-hidden rounded-3xl border bg-surface shadow-card ${ord.status === "READY" ? "border-success/40" : "border-line"}`}
                    >
                      {/* Order header */}
                      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-3 pt-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-xl bg-surface-3 px-2.5 py-1 font-mono text-sm font-bold tracking-tight text-ink" dir="ltr">{ord.order_number}</span>
                          <time className="text-xs text-subtle">{timeOf(ord.created_at)}</time>
                          {ord.mine === false && <StatusPill tone="neutral" size="sm">{t("طلب ضيف آخر", "Another guest's order")}</StatusPill>}
                          {ord.amended && ord.status !== "CANCELLED" && <StatusPill tone="info" size="sm">{t("معدّل", "Changed")}</StatusPill>}
                        </div>
                        <StatusPill tone={tone} dot pulse={pulse}>{badge.label}</StatusPill>
                      </div>

                      {/* Progress: received → in the kitchen → ready */}
                      {step !== null && (
                        <div className="px-5 pb-4">
                          <div className="relative flex items-start justify-between">
                            <div className="absolute inset-x-5 top-4 h-1 rounded-full bg-surface-3" aria-hidden="true">
                              <motion.div
                                className="h-full rounded-full bg-gradient-to-r from-brand to-leaf rtl:bg-gradient-to-l"
                                style={{ originX: dir === "rtl" ? 1 : 0 }}
                                initial={{ scaleX: 0 }}
                                animate={{ scaleX: step / 2 }}
                                transition={{ duration: 0.9, ease: EASE_OUT, delay: 0.15 }}
                              />
                            </div>
                            {steps.map((item, index) => {
                              const done = index < step;
                              const current = index === step;
                              const StepIcon = done ? Check : item.icon;
                              return (
                                <div key={item.label} className="relative z-[1] flex w-20 flex-col items-center gap-1.5 text-center">
                                  <motion.span
                                    initial={false}
                                    animate={{ scale: current ? 1.08 : 1 }}
                                    className={`flex size-9 items-center justify-center rounded-full border-2 transition-colors ${done ? "border-brand bg-brand text-on-brand" : current ? "border-brand bg-surface text-brand shadow-glow" : "border-line bg-surface text-subtle"}`}
                                  >
                                    <StepIcon className="size-4" aria-hidden="true" />
                                  </motion.span>
                                  <span className={`text-[0.6875rem] font-semibold leading-tight ${current ? "text-ink" : done ? "text-ink-2" : "text-subtle"}`}>{item.label}</span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Dishes */}
                      <ul className="divide-y divide-line border-t border-line">
                        {ord.items.map((it, idx) => (
                          <li key={it.item_id ?? idx} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 py-3">
                            <div className="min-w-0 flex-1">
                              <p className="font-semibold text-ink">
                                <span className="me-1.5 tabular-nums text-brand">{it.quantity}×</span>
                                {inLang(it.name_ar, it.name_en)}
                                {it.added_later && <StatusPill tone="accent" size="sm" className="ms-2 align-middle">{t("أُضيف لاحقاً", "Added later")}</StatusPill>}
                              </p>
                              {it.note && <p className="mt-0.5 text-xs text-accent-ink">{t("ملاحظة: ", "Note: ")}{it.note}</p>}
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                              {ord.editable && it.item_id && (
                                <div className="flex items-center gap-1" role="group" aria-label={t(`تعديل ${inLang(it.name_ar, it.name_en)}`, `Change ${inLang(it.name_ar, it.name_en)}`)}>
                                  <QuantityStepper
                                    value={it.quantity}
                                    onDecrement={() => void handleAmendItem(ord, it, it.quantity - 1)}
                                    onIncrement={() => void handleAmendItem(ord, it, it.quantity + 1)}
                                    decrementDisabled={amendingOrderId === ord.order_id}
                                    incrementDisabled={amendingOrderId === ord.order_id || it.quantity >= 50}
                                    size="sm"
                                  />
                                  <button
                                    type="button"
                                    disabled={amendingOrderId === ord.order_id}
                                    onClick={() => void handleAmendItem(ord, it, 0)}
                                    aria-label={t(`حذف ${inLang(it.name_ar, it.name_en)}`, `Remove ${inLang(it.name_ar, it.name_en)}`)}
                                    title={t("حذف", "Remove")}
                                    className="flex size-9 items-center justify-center rounded-full text-subtle transition-colors hover:bg-danger-soft hover:text-danger disabled:opacity-40"
                                  >
                                    <Trash2 className="size-4" aria-hidden="true" />
                                  </button>
                                </div>
                              )}
                              <span className="flex min-w-16 items-center justify-end gap-1.5 text-end font-semibold tabular-nums text-ink">
                                {amendingItemId !== null && amendingItemId === it.item_id && <ButtonSpinner className="size-3.5 text-brand" />}
                                {inLang(it.line_total_display, it.line_total_display_en)}
                              </span>
                            </div>
                          </li>
                        ))}
                      </ul>

                      {ord.editable && (
                        <p className="flex items-start gap-2 border-t border-line bg-surface-2 px-5 py-2.5 text-xs text-muted">
                          <Pencil className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                          {ord.status === "PREPARING"
                            ? t("يمكنك التعديل حتى يجهز الطلب؛ المطبخ سيرى التعديل فوراً.", "You can change it until it's ready; the kitchen will see the change right away.")
                            : t("يمكنك تعديل هذا الطلب حتى يجهز.", "You can change this order until it's ready.")}
                        </p>
                      )}
                      {ord.cancelled_by_guest && <p className="border-t border-line bg-surface-2 px-5 py-2.5 text-sm text-ink-2">{t("ألغيت هذا الطلب.", "You cancelled this order.")}</p>}
                      {ord.cancelled_with_visit && <p className="border-t border-line bg-warning-soft px-5 py-2.5 text-sm text-warning-ink">{t("أُلغي هذا الطلب لأن المطعم أنهى زيارة الطاولة.", "This order was cancelled because the restaurant ended the table visit.")}</p>}

                      {/* Total */}
                      <div className="flex items-center justify-between border-t border-line px-5 py-3.5">
                        <span className="text-sm font-semibold text-muted">{t("إجمالي هذا الطلب", "Order total")}</span>
                        <span className="font-display text-xl font-bold leading-none tabular-nums text-brand">{inLang(ord.total_display_ar, ord.total_display_en)}</span>
                      </div>
                    </motion.article>
                  );
                })}
              </div>
            )}
            </Reveal>
          </motion.section>
          )}
        </div>

        {/* Table service */}
        <aside aria-labelledby="service-title" className="lg:order-none">
          <div className="space-y-4 lg:sticky lg:top-24">
            <section className="rounded-[1.75rem] border border-line bg-surface p-5 shadow-card">
              <div className="mb-4 flex items-start gap-3">
                <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-accent-soft text-accent-ink">
                  <ConciergeBell className="size-5" aria-hidden="true" />
                </span>
                <div>
                  <h2 id="service-title" className="font-display text-xl font-bold leading-tight text-ink">{t("خدمة الطاولة", "Table service")}</h2>
                  <p className="mt-0.5 text-sm text-muted">{t("اطلب مساعدة أو خدمة، ونحن قادمون.", "Ask for help or a service, and we'll be right there.")}</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-2">
                {serviceItems.map((srv) => {
                  const isPending = serviceRequests.some((request) => request.type === srv.type && (request.status === "OPEN" || request.status === "IN_PROGRESS"));
                  const Icon = srv.icon;
                  return (
                    <motion.button
                      key={srv.type}
                      type="button"
                      whileTap={{ scale: 0.96 }}
                      onClick={() => handleQuickService(srv.type, srv.label)}
                      disabled={requestSending || isPending}
                      aria-busy={sendingService === srv.type}
                      className={`relative flex min-h-[5.5rem] flex-col items-start justify-between gap-2 rounded-2xl border p-3.5 text-start transition-colors disabled:cursor-not-allowed ${isPending ? "border-warning/30 bg-warning-soft" : sendingService === srv.type ? "border-brand-line bg-brand-soft/60" : "border-line bg-surface-2 hover:border-brand-line hover:bg-brand-soft/60 disabled:opacity-60"}`}
                    >
                      {sendingService === srv.type
                        ? <ButtonSpinner className="size-5 text-brand" />
                        : <Icon className={`size-5 ${isPending ? "text-warning-ink" : "text-brand"}`} aria-hidden="true" />}
                      <span className="text-sm font-semibold leading-tight text-ink">{srv.label}</span>
                      {isPending && (
                        <span className="flex items-center gap-1.5 text-[0.6875rem] font-bold text-warning-ink">
                          <span className="size-1.5 animate-pulse rounded-full bg-warning" aria-hidden="true" />
                          {t("قيد التنفيذ", "In progress")}
                        </span>
                      )}
                    </motion.button>
                  );
                })}
              </div>
              <button
                type="button"
                onClick={() => setShowComplaintModal(true)}
                className="mt-2.5 flex w-full items-center justify-center gap-2 rounded-2xl border border-line py-3 text-sm font-semibold text-ink-2 transition-colors hover:border-danger/30 hover:bg-danger-soft hover:text-danger-ink"
              >
                <MessageSquareWarning className="size-[18px]" aria-hidden="true" />
                {t("تقديم شكوى", "Make a complaint")}
              </button>
            </section>
          </div>
        </aside>
      </main>

      <Toast message={requestFeedback} />

      {/* Complaint */}
      <Sheet open={showComplaintModal} onClose={() => { if (!complaintSending) setShowComplaintModal(false); }} labelledBy="complaint-modal-title" size="md">
        <SheetHeader
          id="complaint-modal-title"
          icon={<MessageSquareWarning className="size-6" aria-hidden="true" />}
          title={t("تقديم شكوى", "Make a complaint")}
          subtitle={t("اكتب ما حدث حتى يصل بوضوح إلى الإدارة.", "Describe what happened so the restaurant can review it.")}
          onClose={complaintSending ? undefined : () => setShowComplaintModal(false)}
        />
        <SheetBody>
          <label htmlFor="customer-complaint" className="field-label">{t("تفاصيل الشكوى", "Complaint details")}</label>
          <textarea
            id="customer-complaint"
            value={complaintMessage}
            onChange={(event) => setComplaintMessage(event.target.value.slice(0, 500))}
            maxLength={500}
            minLength={3}
            required
            rows={5}
            autoFocus
            placeholder={t("اكتب تفاصيل الشكوى هنا...", "Write your complaint here...")}
            className="input min-h-32"
          />
          <div className="mt-1.5 text-end text-xs tabular-nums text-subtle">{complaintMessage.length}/500</div>
        </SheetBody>
        <SheetFooter>
          <button type="button" onClick={() => setShowComplaintModal(false)} disabled={complaintSending} className="btn btn-secondary">{t("إلغاء", "Cancel")}</button>
          <button type="button" onClick={() => void handleSubmitComplaint()} disabled={complaintSending || complaintMessage.trim().length < 3} className="btn btn-primary flex-1">
            {complaintSending ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Send className="size-[18px] rtl:-scale-x-100" aria-hidden="true" />}
            {complaintSending ? t("جارٍ الإرسال...", "Sending...") : t("إرسال الشكوى", "Send complaint")}
          </button>
        </SheetFooter>
      </Sheet>

      {/* Optional Non-Blocking Feedback Modal (REQ-018) */}
      <Sheet open={showFeedbackModal} onClose={() => setShowFeedbackModal(false)} labelledBy="feedback-modal-title" describedBy="feedback-modal-description" size="sm">
        <SheetBody className="pt-2 text-center sm:pt-8">
          <motion.span
            initial={{ scale: 0.6, rotate: -20, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            transition={spring.pop}
            className="mx-auto mb-3 flex size-16 items-center justify-center rounded-3xl bg-accent-soft text-accent shadow-card"
            aria-hidden="true"
          >
            <Star className="size-8 fill-current" />
          </motion.span>
          <h2 id="feedback-modal-title" className="font-display text-xl font-bold leading-snug text-ink">
            {t("كيف كانت تجربتك في جبران؟", "How was your experience at Jubran?")}
          </h2>
          <p id="feedback-modal-description" className="mx-auto mt-1.5 max-w-xs text-sm text-muted">
            {t(
              "رأيك يهمنا لمواصلة تقديم المذاق الأصيل والخدمة التي تليق بكم (اختياري).",
              "Your feedback helps us maintain our authentic hospitality (optional)."
            )}
          </p>

          {/* Star Rating with Big Tap Areas */}
          <div className="my-5 flex justify-center gap-1" role="group" aria-label={t("التقييم", "Rating")} dir="ltr">
            {[1, 2, 3, 4, 5].map((star) => (
              <motion.button
                key={star}
                type="button"
                onClick={() => setRating(star)}
                whileHover={{ scale: 1.15, y: -2 }}
                whileTap={{ scale: 0.85 }}
                animate={star <= rating ? { scale: [1, 1.25, 1] } : { scale: 1 }}
                transition={spring.pop}
                className="p-1.5"
                aria-label={t(`تقييم ${star} من 5`, `Rate ${star} out of 5`)}
                aria-pressed={star <= rating}
              >
                <Star className={`size-9 transition-colors ${star <= rating ? "fill-accent text-accent" : "text-line-strong"}`} aria-hidden="true" />
              </motion.button>
            ))}
          </div>

          <label htmlFor="feedback-comment" className="sr-only">{t("تعليقك (اختياري)", "Your comment (optional)")}</label>
          <textarea
            id="feedback-comment"
            value={comment}
            onChange={(e) => setComment(e.target.value.slice(0, 500))}
            maxLength={500}
            placeholder={t("اكتب ملاحظاتك أو تعليقك هنا...", "Write your feedback or comments here...")}
            rows={3}
            className="input text-start"
          />
        </SheetBody>
        <SheetFooter>
          <button type="button" onClick={() => setShowFeedbackModal(false)} className="btn btn-ghost">
            {t("تخطي", "Skip")}
          </button>
          <button type="button" onClick={handleSendFeedback} disabled={rating < 1 || feedbackSending} className="btn btn-primary flex-1" aria-busy={feedbackSending}>
            {feedbackSending && <ButtonSpinner className="size-5" />}
            {feedbackSending ? t("جارٍ الإرسال...", "Sending...") : t("إرسال التقييم", "Submit feedback")}
          </button>
        </SheetFooter>
      </Sheet>
    </div>
  );
}
