"use client";

import { useEffect, useState, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import {
  Armchair, BellOff, BellRing, Check, ChefHat, ChevronDown, CircleCheckBig, Clock3, Coffee, ConciergeBell, DoorOpen,
  Flame, HandPlatter, History, LayoutGrid, LoaderCircle, Map as MapIcon, Maximize, Pencil, Play, Radio, ReceiptText,
  RefreshCw, Search, TriangleAlert, X, ZoomIn, ZoomOut,
} from "lucide-react";
import { apiFetch, ApiException } from "@/lib/api";
import { AlertChime, type AlertKeys, alertKeys, newAlerts } from "@/lib/floorAlerts";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { restaurantTime } from "@/lib/time";
import { useLanguage, type Language } from "@/context/LanguageContext";
import { Sheet, SheetBody, SheetFooter, SheetHeader } from "@/components/ui/Sheet";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { StatusPill } from "@/components/ui/StatusPill";
import { Toast } from "@/components/ui/Toast";
import { ButtonSpinner, Skeleton } from "@/components/ui/Feedback";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { EASE_OUT, fadeUp, spring, stagger } from "@/lib/motion";
import { AddedLaterTag, OrderChangeNotice, type OrderAmendment } from "@/components/admin/OrderChanges";
import { TableGuests } from "@/components/admin/TableGuests";
import Image from "next/image";

interface OrderItem {
  item_id?: string;
  added_later?: boolean;
  name_ar: string;
  name_en?: string;
  quantity: number;
  note?: string;
  unit_price_display: string;
  unit_price_display_en?: string;
  line_total_display: string;
  line_total_display_en?: string;
}

interface OrderSummary {
  order_id: string;
  order_number: string;
  table_id: string;
  table_number: string;
  table_session_id: string;
  customer_id: string;
  status: string;
  is_served: boolean;
  total_minor?: number;
  total_display: string;
  total_display_en?: string;
  items: OrderItem[];
  created_at: string;
  served_at?: string | null;
  closure_note?: string | null;
  /** Changes the guest made after sending the order. */
  amendments?: OrderAmendment[];
  /** Changed while the kitchen was preparing it, and nobody pressed "Seen" yet. */
  needs_attention?: boolean;
  cancelled_by_guest?: boolean;
}

interface ServiceRequestItem {
  id: string;
  type: string;
  status: "OPEN" | "IN_PROGRESS" | "RESOLVED" | "CANCELLED";
  created_at: string;
  table_session_id: string;
  customer_id: string;
  closure_note?: string | null;
}

interface ComplaintItem {
  id: string;
  message: string;
  status: "OPEN" | "IN_PROGRESS" | "RESOLVED" | "CANCELLED";
  created_at: string;
  table_session_id: string;
  customer_id: string;
  closure_note?: string | null;
}

interface ServiceQueueItem {
  id: string;
  kind: "SERVICE" | "COMPLAINT";
  type: string;
  status: "OPEN" | "IN_PROGRESS" | "RESOLVED" | "CANCELLED";
  table_id: string;
  table_number: string;
  table_session_id: string;
  customer_id: string;
  message?: string;
  created_at: string;
  resolved_at?: string | null;
  closure_note?: string | null;
}

interface TableFloorItem {
  table_id: string;
  table_number: string;
  shape: "SQUARE" | "ROUND" | "RECTANGLE";
  seat_count: number;
  x_percent: number;
  y_percent: number;
  rotation_deg: number;
  base_state: "VACANT" | "OCCUPIED_IDLE" | "ORDER_PENDING" | "ORDER_PREPARING" | "ORDER_READY";
  active_session_id?: string;
  active_session_ids?: string[];
  overlays: {
    service_requested: boolean;
    bill_requested: boolean;
    has_complaint: boolean;
    service_requests?: ServiceRequestItem[];
    complaints?: ComplaintItem[];
  };
  orders: OrderSummary[];
}

interface FloorSnapshot {
  tables: TableFloorItem[];
  active_orders: OrderSummary[];
  completed_orders: OrderSummary[];
  service_queue: ServiceQueueItem[];
  timestamp: string;
}

const TABLE_SHAPE_LABELS: Record<TableFloorItem["shape"], { ar: string; en: string }> = {
  SQUARE: { ar: "طاولة مربعة", en: "Square table" },
  ROUND: { ar: "طاولة دائرية", en: "Round table" },
  RECTANGLE: { ar: "طاولة مستطيلة", en: "Rectangular table" },
};

/** The English text when the page is in English and the API sent one; otherwise the Arabic. */
function localized(lang: Language, ar: string, en?: string | null): string {
  return lang === "en" && en ? en : ar;
}

/** English count with the right noun: "1 order", "3 orders". */
function countEn(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "مقعد واحد", "مقعدان", "4 مقاعد", "12 مقعداً" ("1 seat", "4 seats" in English) */
function seatsLabel(count: number, lang: Language): string {
  if (lang === "en") return countEn(count, "seat", "seats");
  if (count === 1) return "مقعد واحد";
  if (count === 2) return "مقعدان";
  if (count >= 3 && count <= 10) return `${count} مقاعد`;
  return `${count} مقعداً`;
}

/** The visits currently open at a table (normally one). */
function activeSessionIds(table: TableFloorItem | null | undefined): string[] {
  if (!table) return [];
  return table.active_session_ids ?? (table.active_session_id ? [table.active_session_id] : []);
}

function getCurrentTableOrders(table: TableFloorItem): OrderSummary[] {
  const activeSessionIds_ = activeSessionIds(table);
  return table.orders.filter((order) => activeSessionIds_.includes(order.table_session_id));
}

/** Orders of this visit the kitchen or the floor still has to finish (not served, closed or cancelled). */
function getOpenTableOrders(table: TableFloorItem): OrderSummary[] {
  return getCurrentTableOrders(table).filter((order) => getOrderStage(order) !== "DELIVERED");
}

interface CustomerHistory {
  customer_id: string;
  table_number: string;
  table_session_id: string;
  session_status: "ACTIVE" | "CLOSED";
  session_started_at: string;
  session_closed_at?: string | null;
  orders: { order_id: string; order_number: string; status: string; is_served: boolean; created_at: string; total_display: string; total_display_en?: string; closure_note?: string | null; items: { name_ar: string; name_en?: string; quantity: number }[] }[];
  services: { id: string; type: string; status: string; created_at: string; closure_note?: string | null }[];
  complaints: { id: string; message: string; status: string; created_at: string; closure_note?: string | null }[];
  drafts: { id: string; status: string; created_at: string; updated_at: string }[];
}

type OrderQueueFilter = "PENDING_APPROVAL" | "PREPARING" | "READY" | "DELIVERED";

// Kitchen workflow: new -> preparing -> ready; "served" is only a table marker.
function getOrderStage(order: OrderSummary): OrderQueueFilter {
  if (order.is_served || order.status === "CLOSED" || order.status === "CANCELLED") return "DELIVERED";
  if (order.status === "READY") return "READY";
  return order.status === "PENDING_APPROVAL" ? "PENDING_APPROVAL" : "PREPARING";
}

/**
 * How a table shows on the floor. The server gives the most urgent state; guests with
 * nothing in progress show as either "not ordered yet" or "served", so neither is mistaken
 * for the other.
 */
type TableView = Exclude<TableFloorItem["base_state"], "OCCUPIED_IDLE"> | "NOT_ORDERED" | "SERVED";

function getTableView(table: TableFloorItem): TableView {
  if (table.base_state !== "OCCUPIED_IDLE") return table.base_state;
  return table.orders.some((order) => order.is_served || order.status === "CLOSED") ? "SERVED" : "NOT_ORDERED";
}

/** The order stages a table has right now: one table can be in several at once. */
function getTableStages(table: TableFloorItem) {
  return {
    pending: table.orders.some((order) => order.status === "PENDING_APPROVAL"),
    preparing: table.orders.some((order) => order.status === "PREPARING"),
    ready: table.orders.some((order) => order.status === "READY" && !order.is_served),
  };
}

function getOrderStageLabel(order: OrderSummary, t: (ar: string, en: string) => string): string {
  if (order.status === "CANCELLED") return t("أُلغي مع الجلسة", "Cancelled with the visit");
  if (order.status === "CLOSED") return t("أُغلق مع الجلسة", "Closed with the visit");
  const label = ORDER_STAGE_LABELS[getOrderStage(order)];
  return t(label.ar, label.en);
}

const ORDER_STAGE_LABELS: Record<OrderQueueFilter, { ar: string; en: string }> = {
  PENDING_APPROVAL: { ar: "طلبات جديدة", en: "New orders" },
  PREPARING: { ar: "قيد التحضير", en: "Preparing" },
  READY: { ar: "جاهزة للتقديم", en: "Ready to serve" },
  DELIVERED: { ar: "مكتملة", en: "Completed" },
};

type ServiceQueueFilter = "OPEN" | "IN_PROGRESS" | "RESOLVED";
const SERVICE_STAGE_LABELS: Record<ServiceQueueFilter, { ar: string; en: string }> = {
  OPEN: { ar: "طلبات جديدة", en: "New requests" },
  IN_PROGRESS: { ar: "قيد التنفيذ", en: "In progress" },
  RESOLVED: { ar: "مكتملة", en: "Completed" },
};
const SERVICE_TYPE_LABELS: Record<string, { ar: string; en: string }> = {
  STAFF: { ar: "طلب موظف", en: "Staff call" },
  TISSUES: { ar: "مناديل إضافية", en: "Extra tissues" },
  CLEAN_TABLE: { ar: "تنظيف الطاولة", en: "Clean the table" },
  BILL: { ar: "طلب الحساب", en: "Bill request" },
  COMPLAINT: { ar: "شكوى", en: "Complaint" },
};

// Table hotspots are sized relative to the map (cqw = 1% of the map's width), so they
// shrink with it on a phone and grow when zoomed, never covering each other.
// Full size (76×64 px) on the 1024 px wide artwork.
const TABLE_HOTSPOT_WIDTH = "clamp(30px, 7.4cqw, 110px)";
const TABLE_HOTSPOT_HEIGHT = "clamp(26px, 6.25cqw, 92px)";

/** One order in the "incoming floor orders" queue (its own component keeps the page small). */
/** Placeholder rows for a queue while the floor's first snapshot loads. */
function QueueSkeleton() {
  return (
    <motion.div exit={{ opacity: 0, transition: { duration: 0.15 } }} className="space-y-2.5" aria-hidden="true">
      {[0, 1].map((row) => (
        <div key={row} className="space-y-2.5 rounded-2xl border border-line bg-surface-2 p-3.5">
          <div className="flex items-center justify-between gap-3">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-3.5 w-12" />
          </div>
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </motion.div>
  );
}

function IncomingOrderCard({ ord, busy, pending, onStartPreparing, onMarkReady, onMarkServed, onAcknowledge }: {
  ord: OrderSummary;
  busy: boolean;
  /** The action on its way right now (e.g. "ready:<order id>"), shown on its button. */
  pending: string | null;
  onStartPreparing: (orderId: string) => void;
  onMarkReady: (orderId: string) => void;
  onMarkServed: (orderId: string) => void;
  onAcknowledge: (orderId: string) => void;
}) {
  const { lang, t } = useLanguage();
  const timeLocale = lang === "ar" ? "ar-JO" : "en-GB";
  const stage = getOrderStage(ord);
  const accent = stage === "PENDING_APPROVAL" ? "before:bg-warning" : stage === "PREPARING" ? "before:bg-preparing" : stage === "READY" ? "before:bg-success" : "before:bg-line-strong";
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 24, transition: { duration: 0.18 } }}
      transition={spring.smooth}
      className={`relative overflow-hidden rounded-2xl border p-3.5 text-sm before:absolute before:inset-y-0 before:start-0 before:w-1 ${accent} ${ord.needs_attention ? "border-danger/50 bg-danger-soft/40 shadow-[0_0_0_3px_color-mix(in_oklab,var(--danger)_18%,transparent)]" : "border-line bg-surface-2 hover:border-line-strong"}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2 border-b border-line pb-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="rounded-lg bg-brand px-2 py-0.5 text-xs font-bold text-on-brand" dir="ltr">{ord.table_number}</span>
          <span className="truncate font-mono text-sm font-bold text-ink" dir="ltr">{ord.order_number}</span>
        </div>
        <time className="shrink-0 text-xs tabular-nums text-subtle">
          {restaurantTime(ord.created_at, timeLocale, { hour: "2-digit", minute: "2-digit" })}
        </time>
      </div>

      <p className="mb-2 select-all break-all text-[0.6875rem] text-subtle" title={t(`معرّف العميل الكامل: ${ord.customer_id}`, `Full customer ID: ${ord.customer_id}`)}>{t("معرّف العميل:", "Customer ID:")} {ord.customer_id}</p>

      <ul className="mb-2 space-y-1">
        {ord.items.map((it, idx) => (
          <li key={idx} className="flex items-start justify-between gap-2 text-[0.8125rem]">
            <div className="min-w-0">
              <span className="font-semibold text-ink"><span className="tabular-nums text-brand">{it.quantity}×</span> {localized(lang, it.name_ar, it.name_en)}</span>
              <AddedLaterTag show={it.added_later} />
              {it.note && <span className="block text-xs text-accent-ink">{t("ملاحظة:", "Note:")} {it.note}</span>}
            </div>
            <span className="shrink-0 font-medium tabular-nums text-muted">{localized(lang, it.line_total_display, it.line_total_display_en)}</span>
          </li>
        ))}
      </ul>

      <div className="mb-2 text-xs font-bold text-ink-2">
        {getOrderStageLabel(ord, t)}
        {ord.is_served && ord.served_at && (
          <span className="ms-2 font-normal text-subtle">
            {restaurantTime(ord.served_at, timeLocale, { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}
      </div>
      <OrderChangeNotice order={ord} onAcknowledge={onAcknowledge} busy={busy} pending={pending === `seen:${ord.order_id}`} />
      {ord.closure_note && !ord.cancelled_by_guest && <p className="mb-2 rounded-xl bg-warning-soft p-2 text-xs text-warning-ink">{ord.closure_note}</p>}

      <div className="flex items-center justify-between gap-2 border-t border-line pt-2.5">
        <span className="font-display text-base font-bold tabular-nums text-brand">{localized(lang, ord.total_display, ord.total_display_en)}</span>
        <div className="flex gap-1.5">
          {ord.status === "PENDING_APPROVAL" && (
            <button onClick={() => onStartPreparing(ord.order_id)} disabled={busy} className="btn btn-sm min-h-8 rounded-lg bg-preparing px-3 text-white hover:brightness-110">
              {pending === `prepare:${ord.order_id}` ? <ButtonSpinner /> : <ChefHat className="size-4" aria-hidden="true" />}
              {t("بدء التحضير", "Start preparing")}
            </button>
          )}
          {ord.status === "PREPARING" && (
            <button onClick={() => onMarkReady(ord.order_id)} disabled={busy} className="btn btn-sm min-h-8 rounded-lg bg-success px-3 text-white hover:brightness-110">
              {pending === `ready:${ord.order_id}` ? <ButtonSpinner /> : <BellRing className="size-4" aria-hidden="true" />}
              {t("جاهز", "Ready")}
            </button>
          )}
          {ord.status === "READY" && !ord.is_served && (
            <button onClick={() => onMarkServed(ord.order_id)} disabled={busy} className="btn btn-primary btn-sm min-h-8 rounded-lg px-3">
              {pending === `served:${ord.order_id}` ? <ButtonSpinner /> : <HandPlatter className="size-4" aria-hidden="true" />}
              {t("تم التقديم", "Served")}
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export default function AdminFloorPage() {
  const router = useRouter();
  const { lang, dir, t } = useLanguage();
  const timeLocale = lang === "ar" ? "ar-JO" : "en-GB";
  const { showConfirm, showChoice } = useGlobalDialog();
  const [snapshot, setSnapshot] = useState<FloorSnapshot | null>(null);
  const [selectedTableId, setSelectedTableId] = useState<string | null>(null);
  const selectedTable = useMemo(
    () => snapshot?.tables.find((table) => table.table_id === selectedTableId) ?? null,
    [snapshot, selectedTableId]
  );
  const currentTableSessionIds = activeSessionIds(selectedTable);
  const currentTableOrders = selectedTable ? getCurrentTableOrders(selectedTable) : [];
  const currentTableServices = (selectedTable?.overlays.service_requests ?? []).filter((request) => currentTableSessionIds.includes(request.table_session_id));
  const currentTableComplaints = (selectedTable?.overlays.complaints ?? []).filter((complaint) => currentTableSessionIds.includes(complaint.table_session_id));
  const [hoveredTable, setHoveredTable] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  // Which action is on its way (its button shows it), e.g. "ready:<order id>".
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  // Failed floor loads in a row (retried by itself until the first snapshot arrives).
  const [floorFailures, setFloorFailures] = useState(0);
  const [customerHistory, setCustomerHistory] = useState<CustomerHistory | null>(null);
  const [customerHistoryLoading, setCustomerHistoryLoading] = useState(false);
  const [historyLoadingFor, setHistoryLoadingFor] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ text: string; type: "success" | "error" | "info" } | null>(null);

  // Layout: "split" (map + side panel) or "grid" (table cards)
  const [viewMode, setViewMode] = useState<"split" | "grid">("split");
  const [zoomLevel, setZoomLevel] = useState<number>(1);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [orderQueueFilter, setOrderQueueFilter] = useState<OrderQueueFilter>("PENDING_APPROVAL");
  const [serviceQueueFilter, setServiceQueueFilter] = useState<ServiceQueueFilter>("OPEN");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [soundEnabled, setSoundEnabled] = useState<boolean>(true);


  const containerRef = useRef<HTMLDivElement>(null);
  const alertKeysRef = useRef<AlertKeys | null>(null);
  // Read by the socket and the timer, which keep the first render's functions.
  const soundEnabledRef = useRef(true);
  const chimeRef = useRef<AlertChime | null>(null);

  useEffect(() => {
    soundEnabledRef.current = soundEnabled;
  }, [soundEnabled]);

  // One audio context for the page, unlocked by the admin's first click or key press.
  useEffect(() => {
    const chime = new AlertChime();
    chimeRef.current = chime;
    const unlock = () => chime.unlock();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
      chime.close();
      chimeRef.current = null;
    };
  }, []);

  const playAlertChime = (type: "order" | "call", force = false) => {
    if (!soundEnabledRef.current && !force) return;
    chimeRef.current?.play(type);
  };

  const showNotification = (text: string, type: "success" | "error" | "info" = "success") => {
    setActionMsg({ text, type });
    setTimeout(() => setActionMsg(null), 3500);
  };

  const fetchFloor = async (isManual = false) => {
    try {
      if (isManual) setActionLoading(true);
      const data = await apiFetch<FloorSnapshot>("/admin/floor");

      // Ring for orders and guest calls that were not there before (compared one by one).
      const keys = alertKeys(data);
      const fresh = newAlerts(alertKeysRef.current, keys);
      alertKeysRef.current = keys;
      if (fresh.order) playAlertChime("order");
      else if (fresh.call) playAlertChime("call");

      // Fetched only on a live event or the slow safety poll, so simply show what arrived.
      setSnapshot(data);
      setFloorFailures(0);

    } catch (err: unknown) {
      if (err instanceof ApiException && (err.status === 401 || err.status === 403)) {
        router.push("/login");
        return;
      }
      console.error("Floor fetch error:", err);
      setFloorFailures((count) => count + 1);
    } finally {
      if (isManual) setActionLoading(false);
    }
  };

  const refreshSoonRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Timers and the socket always call the latest version of fetchFloor.
  const fetchFloorRef = useRef(fetchFloor);
  useEffect(() => {
    fetchFloorRef.current = fetchFloor;
  });

  useEffect(() => {
    // AdminGate (app/admin/layout.tsx) has already confirmed the admin login.
    void Promise.resolve().then(() => fetchFloorRef.current());
    // Changes arrive over the live socket below; this slow poll is only a safety net.
    const timer = setInterval(() => void fetchFloorRef.current(false), 30000);
    return () => {
      clearInterval(timer);
      if (refreshSoonRef.current) clearTimeout(refreshSoonRef.current);
    };
  }, []);

  // Live channel (accepted only for a signed-in admin; the login cookie is sent
  // automatically). Several events often arrive together, e.g. closing a table:
  // refresh once. After a reconnect, reload whatever was missed.
  useAutoRetry(snapshot ? 0 : floorFailures, () => void fetchFloorRef.current(false));

  useLiveSocket("/ws/admin", () => {
    if (refreshSoonRef.current) clearTimeout(refreshSoonRef.current);
    refreshSoonRef.current = setTimeout(() => void fetchFloorRef.current(false), 250);
  }, () => void fetchFloorRef.current(false));

  // Table Operational Handlers
  const handleStartPreparing = async (orderId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`prepare:${orderId}`);
      await apiFetch(`/admin/orders/${orderId}/start-preparing`, { method: "POST" });
      showNotification(t("🍳 تم بدء تحضير الطلب في المطبخ", "🍳 The kitchen has started preparing the order"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleMarkReady = async (orderId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`ready:${orderId}`);
      await apiFetch(`/admin/orders/${orderId}/mark-ready`, { method: "POST" });
      showNotification(t("✅ الطلب جاهز، والزبون يرى الآن أنه جاهز", "✅ Order is ready, and the guest can now see that it is ready"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleMarkServed = async (orderId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`served:${orderId}`);
      await apiFetch(`/admin/orders/${orderId}/mark-served`, { method: "POST" });
      showNotification(t("🍽️ تم تسجيل تسليم الطلب للزبائن بنجاح", "🍽️ Order marked as served to the guests"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleCloseSession = async (sessionId: string, tableNumber: string) => {
    const unfinishedOrders = currentTableOrders.filter((order) => !order.is_served && order.status !== "CLOSED" && order.status !== "CANCELLED");
    const pendingOrders = unfinishedOrders.filter((order) => order.status === "PENDING_APPROVAL");
    const kitchenOrders = unfinishedOrders.filter((order) => order.status === "PREPARING" || order.status === "READY");
    const unfinishedServices = currentTableServices.filter((request) => request.status === "OPEN" || request.status === "IN_PROGRESS");
    const openComplaints = currentTableComplaints.filter((complaint) => complaint.status === "OPEN" || complaint.status === "IN_PROGRESS");
    const describeOrder = (order: OrderSummary) => `• ${order.order_number}: ${order.items.map((item) => `${item.quantity}× ${localized(lang, item.name_ar, item.name_en)}`).join(t("، ", ", "))}`;
    const serviceNames: Record<string, string> = { STAFF: t("طلب موظف", "Staff call"), TISSUES: t("مناديل إضافية", "Extra tissues"), CLEAN_TABLE: t("تنظيف الطاولة", "Clean the table"), BILL: t("طلب الحساب", "Bill request") };

    const sections: string[] = [];
    if (kitchenOrders.length) sections.push(`${t("طلبات قيد التحضير أو جاهزة ولم تُسلَّم:", "Orders being prepared or ready but not served:")}\n${kitchenOrders.map(describeOrder).join("\n")}`);
    if (pendingOrders.length) sections.push(`${t("طلبات لم يبدأ تحضيرها (ستُلغى):", "Orders not started yet (will be cancelled):")}\n${pendingOrders.map(describeOrder).join("\n")}`);
    if (unfinishedServices.length) sections.push(`${t("طلبات خدمة مفتوحة (ستُلغى):", "Open service requests (will be cancelled):")}\n${unfinishedServices.map((request) => `• ${serviceNames[request.type] ?? t("طلب خدمة", "Service request")}`).join("\n")}`);
    if (openComplaints.length) sections.push(`${t("شكاوى مفتوحة (ستبقى في قائمة المتابعة):", "Open complaints (will stay on the follow-up list):")}\n${openComplaints.map((complaint) => `• ${complaint.message}`).join("\n")}`);
    const summary = sections.length
      ? `${t(`على طاولة ${tableNumber}:`, `At table ${tableNumber}:`)}\n\n${sections.join("\n\n")}`
      : t(`هل أنت متأكد من إنهاء جلسة طاولة ${tableNumber} وتفريغها للزبائن الجدد؟`, `End the visit at table ${tableNumber} and free it for new guests?`);

    let kitchenDecision: string | null = null;
    if (kitchenOrders.length) {
      kitchenDecision = await showChoice(`${summary}\n\n${t("ماذا حدث للطلبات قيد التحضير أو الجاهزة؟", "What happened to the orders being prepared or ready?")}`, [
        { value: "delivered", label: t("سُلِّمت للزبون وإنهاء الجلسة", "Served to the guest, end the visit") },
        { value: "cancel", label: t("إلغاؤها وإنهاء الجلسة", "Cancel them and end the visit"), tone: "danger" },
      ], { title: t(`إنهاء جلسة طاولة ${tableNumber}`, `End visit at table ${tableNumber}`), cancelLabel: t("رجوع", "Back") });
      if (!kitchenDecision) return;
    } else if (!await showConfirm(summary, { title: t(`إنهاء جلسة طاولة ${tableNumber}`, `End visit at table ${tableNumber}`), tone: "danger", confirmLabel: t("إنهاء الجلسة", "End visit") })) {
      return;
    }
    try {
      setActionLoading(true);
      setPendingAction(`close:${sessionId}`);
      await apiFetch(`/admin/table-sessions/${sessionId}/close`, {
        method: "POST",
        body: JSON.stringify(kitchenDecision ? { kitchen_orders: kitchenDecision } : {}),
      });
      setSelectedTableId(null);
      showNotification(t(`✅ تم إغلاق جلسة طاولة ${tableNumber} وإعادتها لحالة فارغة`, `✅ Visit at table ${tableNumber} closed; the table is vacant again`));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      // e.g. the kitchen started an order while this dialog was open: refresh so the choice is offered.
      if (err instanceof ApiException) showNotification(err.message, "error");
      await fetchFloorRef.current(true);
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleAcknowledgeChanges = async (orderId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`seen:${orderId}`);
      await apiFetch(`/admin/orders/${orderId}/acknowledge-changes`, { method: "POST" });
      showNotification(t("تم تسجيل الاطلاع على تعديل الزبون", "Marked the guest's change as seen"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleLookupCustomerHistory = async (id?: string) => {
    const customerId = (id ?? searchQuery).trim();
    if (!customerId) return;
    try {
      setCustomerHistoryLoading(true);
      setHistoryLoadingFor(customerId);
      const history = await apiFetch<CustomerHistory>(`/admin/customer-sessions/${encodeURIComponent(customerId)}/history`);
      setCustomerHistory(history);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
      else showNotification(t("تعذر تحميل سجل العميل", "Could not load the customer history"), "error");
    } finally {
      setCustomerHistoryLoading(false);
      setHistoryLoadingFor(null);
    }
  };

  // Resolve Service / Bill / Complaint Handlers
  const handleStartService = async (requestId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`service-start:${requestId}`);
      await apiFetch(`/admin/service-requests/${requestId}/start`, { method: "POST" });
      showNotification(t("تم بدء معالجة طلب الخدمة", "Started handling the service request"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleResolveService = async (requestId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`service-done:${requestId}`);
      await apiFetch(`/admin/service-requests/${requestId}/resolve`, { method: "POST" });
      showNotification(t("🛎️ تم تلبية طلب الخدمة وإغلاقه", "🛎️ Service request done and closed"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleStartComplaint = async (complaintId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`complaint-start:${complaintId}`);
      await apiFetch(`/admin/complaints/${complaintId}/start`, { method: "POST" });
      showNotification(t("تم بدء معالجة الشكوى", "Started handling the complaint"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const handleResolveComplaint = async (complaintId: string) => {
    try {
      setActionLoading(true);
      setPendingAction(`complaint-done:${complaintId}`);
      await apiFetch(`/admin/complaints/${complaintId}/resolve`, { method: "POST" });
      showNotification(t("⚠️ تم معالجة الشكوى بنجاح", "⚠️ Complaint resolved"));
      await fetchFloorRef.current(true);
    } catch (err: unknown) {
      if (err instanceof ApiException) showNotification(err.message, "error");
    } finally {
      setActionLoading(false);
      setPendingAction(null);
    }
  };

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  // Visual status stylings
  const getStatusStyles = (state: string) => {
    switch (state) {
      case "VACANT":
        return {
          pillBg: "bg-red-700 text-white",
          dotColor: "bg-red-300",
          text: t("فارغة", "Vacant"),
          ringColor: "rgba(185, 28, 28, 0.8)",
          glowColor: "rgba(220, 38, 38, 0.3)",
          borderColor: "border-red-400",
        };
      case "NOT_ORDERED":
        return {
          pillBg: "bg-blue-800 text-white",
          dotColor: "bg-blue-300",
          text: t("لم يطلب بعد", "Not ordered yet"),
          ringColor: "rgba(37, 99, 235, 0.85)",
          glowColor: "rgba(37, 99, 235, 0.36)",
          borderColor: "border-blue-500",
        };
      case "SERVED":
        return {
          pillBg: "bg-violet-700 text-white",
          dotColor: "bg-violet-300",
          text: t("تم التقديم", "Served"),
          ringColor: "rgba(109, 40, 217, 0.85)",
          glowColor: "rgba(124, 58, 237, 0.36)",
          borderColor: "border-violet-500",
        };
      case "ORDER_PENDING":
        return {
          pillBg: "bg-yellow-300 text-[#302000] animate-pulse",
          dotColor: "bg-yellow-200",
          text: t("طلب جديد", "New order"),
          ringColor: "rgba(234, 179, 8, 0.98)",
          glowColor: "rgba(250, 204, 21, 0.5)",
          borderColor: "border-yellow-300",
        };
      case "ORDER_PREPARING":
        return {
          pillBg: "bg-orange-800 text-white",
          dotColor: "bg-orange-300",
          text: t("قيد التحضير", "Preparing"),
          ringColor: "rgba(194, 65, 12, 0.96)",
          glowColor: "rgba(194, 65, 12, 0.42)",
          borderColor: "border-orange-700",
        };
      case "ORDER_READY":
        return {
          pillBg: "bg-green-700 text-white",
          dotColor: "bg-green-300",
          text: t("جاهز للتقديم", "Ready to serve"),
          ringColor: "rgba(21, 128, 61, 0.95)",
          glowColor: "rgba(22, 163, 74, 0.42)",
          borderColor: "border-green-500",
        };
      default:
        return {
          pillBg: "bg-gray-600 text-white",
          dotColor: "bg-gray-400",
          text: state,
          ringColor: "rgba(107, 114, 128, 0.3)",
          glowColor: "rgba(107, 114, 128, 0.15)",
          borderColor: "border-gray-400",
        };
    }
  };

  // Filtered tables list
  const filteredTables = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.tables.filter((t) => {
      // Search
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const matchesNum = t.table_number.toLowerCase().includes(q);
        const matchesId = t.table_id.toLowerCase().includes(q);
        const matchesCustomer = [...t.orders.map((order) => order.customer_id), ...(t.overlays.service_requests ?? []).map((request) => request.customer_id), ...(t.overlays.complaints ?? []).map((complaint) => complaint.customer_id)].some((customerId) => customerId.toLowerCase().includes(q));
        if (!matchesNum && !matchesId && !matchesCustomer) return false;
      }
      // Status: a table shows under every order stage it has (a new order and one in the kitchen: both)
      if (statusFilter === "ALL") return true;
      const stages = getTableStages(t);
      if (statusFilter === "ATTENTION") {
        return stages.pending || t.overlays.service_requested || t.overlays.bill_requested || t.overlays.has_complaint;
      }
      if (statusFilter === "ORDER_PENDING") return stages.pending;
      if (statusFilter === "ORDER_PREPARING") return stages.preparing;
      if (statusFilter === "ORDER_READY") return stages.ready;
      return getTableView(t) === statusFilter;
    });
  }, [snapshot, statusFilter, searchQuery]);

  // Aggregate floor statistics
  const stats = useMemo(() => {
    if (!snapshot) return { total: 0, occupied: 0, vacant: 0, pendingOrders: 0, preparingOrders: 0, readyOrders: 0, alerts: 0 };
    const total = snapshot.tables.length;
    const occupied = snapshot.tables.filter((t) => t.base_state !== "VACANT").length;
    const vacant = total - occupied;
    // Counted like the filters: every table with such an order, even if something more urgent shows.
    const stages = snapshot.tables.map(getTableStages);
    const pendingOrders = stages.filter((stage) => stage.pending).length;
    const preparingOrders = stages.filter((stage) => stage.preparing).length;
    const readyOrders = stages.filter((stage) => stage.ready).length;
    const alerts = snapshot.tables.filter(
      (t) => t.overlays.service_requested || t.overlays.bill_requested || t.overlays.has_complaint
    ).length;
    return { total, occupied, vacant, pendingOrders, preparingOrders, readyOrders, alerts };
  }, [snapshot]);

  const queueOrders = useMemo(() => {
    if (!snapshot) return [];
    const query = searchQuery.trim().toLowerCase();
    return [...snapshot.active_orders, ...snapshot.completed_orders]
      .filter((order) => getOrderStage(order) === orderQueueFilter)
      .filter((order) => !query || order.customer_id.toLowerCase().includes(query) || order.table_number.toLowerCase().includes(query) || order.order_number.toLowerCase().includes(query));
  }, [snapshot, orderQueueFilter, searchQuery]);

  const queueCounts = useMemo(() => {
    const orders = snapshot ? [...snapshot.active_orders, ...snapshot.completed_orders] : [];
    return {
      PENDING_APPROVAL: orders.filter((order) => getOrderStage(order) === "PENDING_APPROVAL").length,
      PREPARING: orders.filter((order) => getOrderStage(order) === "PREPARING").length,
      READY: orders.filter((order) => getOrderStage(order) === "READY").length,
      DELIVERED: orders.filter((order) => getOrderStage(order) === "DELIVERED").length,
    };
  }, [snapshot]);

  const serviceQueue = snapshot?.service_queue ?? [];
  const query = searchQuery.trim().toLowerCase();
  const matchesCustomerOrTable = (item: ServiceQueueItem) => !query || item.customer_id.toLowerCase().includes(query) || item.table_number.toLowerCase().includes(query);
  const filteredServiceQueue = serviceQueue.filter((item) => item.status === serviceQueueFilter && matchesCustomerOrTable(item));
  const cancelledServices = serviceQueue.filter((item) => item.status === "CANCELLED" && matchesCustomerOrTable(item));

  const statusFilters = [
    { id: "ALL", label: t(`الكل (${stats.total})`, `All (${stats.total})`) },
    { id: "ATTENTION", label: t("بحاجة لإجراء", "Needs action"), hot: true },
    { id: "ORDER_PENDING", label: t("طلبات جديدة", "New orders") },
    { id: "ORDER_PREPARING", label: t("قيد التحضير", "Preparing") },
    { id: "ORDER_READY", label: t("جاهزة", "Ready") },
    { id: "SERVED", label: t("تم التقديم", "Served") },
    { id: "NOT_ORDERED", label: t("لم يطلب بعد", "Not ordered yet") },
    { id: "VACANT", label: t("فارغة", "Vacant") },
  ];
  const kpis = [
    { id: "occupancy", icon: Armchair, label: t("إشغال الصالة", "Occupancy"), value: `${stats.occupied}/${stats.total}`, hint: `${stats.total ? Math.round((stats.occupied / stats.total) * 100) : 0}%`, tone: "brand" as const, show: true },
    { id: "pending", icon: Clock3, label: t("طلبات جديدة", "New orders"), value: String(stats.pendingOrders), tone: "warning" as const, show: true, pulse: stats.pendingOrders > 0 },
    { id: "preparing", icon: ChefHat, label: t("قيد التحضير", "Preparing"), value: String(stats.preparingOrders), tone: "preparing" as const, show: true },
    { id: "ready", icon: BellRing, label: t("جاهزة للتقديم", "Ready to serve"), value: String(stats.readyOrders), tone: "success" as const, show: true },
    { id: "alerts", icon: TriangleAlert, label: t("نداءات عاجلة", "Urgent calls"), value: String(stats.alerts), tone: "danger" as const, show: true, pulse: stats.alerts > 0 },
  ];
  const kpiTone = {
    brand: "bg-brand-soft text-brand-soft-ink",
    warning: "bg-warning-soft text-warning-ink",
    preparing: "bg-preparing-soft text-preparing-ink",
    success: "bg-success-soft text-success-ink",
    danger: "bg-danger-soft text-danger-ink",
  };
  const legend = [
    { dot: "bg-red-500", label: t("فارغة", "Vacant") },
    { dot: "bg-blue-500", label: t("لم يطلب بعد", "Not ordered yet") },
    { dot: "bg-yellow-300 animate-pulse", label: t("طلب جديد", "New order") },
    { dot: "bg-orange-600", label: t("قيد التحضير", "Preparing") },
    { dot: "bg-green-500", label: t("جاهز", "Ready") },
    { dot: "bg-violet-500", label: t("تم التقديم", "Served") },
  ];
  const toastTone = actionMsg?.type === "error" ? "error" : actionMsg?.type === "info" ? "info" : "success";

  return (
    <div className="flex flex-1 flex-col">
      <Toast message={actionMsg?.text} tone={toastTone} />

      {/* Main Floor Container */}
      <main className="mx-auto flex w-full max-w-[1720px] flex-1 flex-col gap-4 px-3 pb-28 pt-4 sm:px-5 lg:px-6 lg:pb-8" dir={dir}>
        {/* Title, live counters and tools */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: EASE_OUT }}
          className="rounded-[1.75rem] border border-line bg-surface p-4 shadow-card sm:p-5"
        >
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex items-center gap-3.5">
              <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow" aria-hidden="true">
                <MapIcon className="size-6" />
              </span>
              <div>
                <div className="flex items-center gap-2.5">
                  <h1 className="font-display text-xl font-bold text-ink sm:text-2xl">{t("صالة جبران المباشرة", "Jubran Live Floor")}</h1>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-1 text-[0.6875rem] font-bold text-success-ink">
                    <span className="relative flex size-2" aria-hidden="true">
                      <span className="absolute inset-0 animate-ping rounded-full bg-success opacity-60" />
                      <span className="relative size-2 rounded-full bg-success" />
                    </span>
                    {t("مباشر", "Live")}
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-muted">
                  {snapshot
                    ? t(`مخطط الصالة • متابعة مباشرة لـ ${stats.total} طاولة`, `Floor plan • live tracking of ${countEn(stats.total, "table", "tables")}`)
                    : t("مخطط الصالة • متابعة مباشرة للطاولات", "Floor plan • live tracking of tables")}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <SegmentedControl
                value={viewMode}
                onChange={setViewMode}
                ariaLabel={t("طريقة العرض", "View")}
                size="sm"
                segments={[
                  { value: "split", label: t("العمليات", "Operations"), icon: <MapIcon className="size-4" aria-hidden="true" /> },
                  { value: "grid", label: t("البطاقات", "Cards"), icon: <LayoutGrid className="size-4" aria-hidden="true" /> },
                ]}
              />
              {/* Sound Toggle */}
              <button
                onClick={() => {
                  const nextState = !soundEnabled;
                  setSoundEnabled(nextState);
                  soundEnabledRef.current = nextState;
                  if (nextState) {
                    chimeRef.current?.unlock();
                    playAlertChime("order", true);
                    showNotification(t("🔔 تم تفعيل التنبيهات الصوتية بنجاح", "🔔 Sound alerts turned on"), "info");
                  } else {
                    showNotification(t("🔕 تم كتم التنبيهات الصوتية", "🔕 Sound alerts muted"), "info");
                  }
                }}
                title={soundEnabled ? t("كتم التنبيهات الصوتية", "Mute sound alerts") : t("تفعيل التنبيهات الصوتية", "Turn on sound alerts")}
                aria-label={soundEnabled ? t("كتم التنبيهات الصوتية", "Mute sound alerts") : t("تفعيل التنبيهات الصوتية", "Turn on sound alerts")}
                aria-pressed={soundEnabled}
                className={`flex size-10 items-center justify-center rounded-xl border transition-colors ${soundEnabled ? "border-brand-line bg-brand-soft text-brand-soft-ink" : "border-line bg-surface-3 text-subtle"}`}
              >
                <AnimatePresence mode="popLayout" initial={false}>
                  <motion.span key={soundEnabled ? "on" : "off"} initial={{ scale: 0.5, rotate: -30, opacity: 0 }} animate={{ scale: 1, rotate: 0, opacity: 1 }} exit={{ scale: 0.5, opacity: 0 }} transition={spring.pop}>
                    {soundEnabled ? <BellRing className="size-[18px]" aria-hidden="true" /> : <BellOff className="size-[18px]" aria-hidden="true" />}
                  </motion.span>
                </AnimatePresence>
              </button>
              {/* Manual Refresh */}
              <button
                onClick={() => void fetchFloorRef.current(true)}
                disabled={actionLoading}
                title={t("تحديث بيانات الصالة فوراً", "Refresh the floor now")}
                aria-label={t("تحديث بيانات الصالة فوراً", "Refresh the floor now")}
                className="flex size-10 items-center justify-center rounded-xl border border-line bg-surface text-ink-2 transition-colors hover:bg-surface-3 disabled:opacity-50"
              >
                <RefreshCw className={`size-[18px] ${actionLoading ? "animate-spin" : ""}`} aria-hidden="true" />
              </button>
            </div>
          </div>

          {/* Real-time operational counters */}
          <motion.ul initial="hidden" animate="show" variants={stagger(0.1, 0.05)} className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
            {kpis.map((kpi) => (
              <motion.li key={kpi.id} variants={fadeUp} className="flex items-center gap-3 rounded-2xl border border-line bg-surface-2 p-3 last:col-span-2 sm:last:col-span-1">
                <span className={`relative flex size-10 shrink-0 items-center justify-center rounded-xl ${kpiTone[kpi.tone]}`}>
                  {kpi.pulse && <span className="absolute inset-0 animate-ring rounded-xl bg-current opacity-20" aria-hidden="true" />}
                  <kpi.icon className="relative size-5" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-muted">{kpi.label}</p>
                  {snapshot ? (
                    <motion.p initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE_OUT }} className="flex items-baseline gap-1.5">
                      <AnimatePresence mode="popLayout" initial={false}>
                        <motion.span key={kpi.value} initial={{ y: 10, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -10, opacity: 0 }} transition={spring.snappy} className="font-display text-xl font-bold tabular-nums text-ink" dir="ltr">
                          {kpi.value}
                        </motion.span>
                      </AnimatePresence>
                      {kpi.hint && <span className="text-xs font-semibold text-subtle">{kpi.hint}</span>}
                    </motion.p>
                  ) : (
                    <Skeleton className="mt-1.5 h-5 w-12" />
                  )}
                </div>
              </motion.li>
            ))}
          </motion.ul>
        </motion.section>

        {/* Status Filters & Search Bar */}
        <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
          <div className="no-scrollbar fade-x -mx-1 flex items-center gap-1.5 overflow-x-auto px-1" role="group" aria-label={t("تصفية الطاولات", "Filter tables")}>
            {statusFilters.map((f) => {
              const active = statusFilter === f.id;
              return (
                <button
                  key={f.id}
                  onClick={() => setStatusFilter(f.id)}
                  aria-pressed={active}
                  className={`relative isolate flex h-10 shrink-0 items-center gap-1.5 rounded-full border px-4 text-sm font-semibold transition-colors ${active ? "border-transparent text-on-brand" : "border-line bg-surface text-ink-2 hover:border-line-strong"}`}
                >
                  {active && <motion.span layoutId="floor-filter" className="absolute inset-0 -z-10 rounded-full bg-brand shadow-glow" transition={spring.snappy} />}
                  {f.hot && <Flame className={`size-4 ${active ? "" : "text-preparing"}`} aria-hidden="true" />}
                  {f.label}
                </button>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-2 lg:flex-nowrap">
            <div className="relative min-w-[16rem] flex-1 lg:w-80 lg:flex-none">
              <Search className="pointer-events-none absolute start-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" aria-hidden="true" />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder={t("ابحث برقم الطاولة أو معرّف العميل", "Table number or customer ID")}
                className="input min-h-10 rounded-full ps-11 pe-10"
                aria-label={t("البحث برقم الطاولة أو معرّف العميل", "Search by table number or customer ID")}
              />
              {searchQuery && (
                <button type="button" onClick={() => setSearchQuery("")} className="absolute end-2 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-subtle hover:bg-surface-3 hover:text-ink" aria-label={t("مسح البحث", "Clear search")}>
                  <X className="size-4" aria-hidden="true" />
                </button>
              )}
            </div>
            <AnimatePresence>
              {searchQuery.trim() && (
                <motion.button
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  type="button"
                  onClick={() => void handleLookupCustomerHistory()}
                  disabled={customerHistoryLoading}
                  className="btn btn-primary btn-sm h-10 rounded-full"
                >
                  {customerHistoryLoading ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <History className="size-4" aria-hidden="true" />}
                  {customerHistoryLoading ? t("جارٍ تحميل السجل…", "Loading history…") : t("عرض سجل هذا العميل", "Show this customer's history")}
                </motion.button>
              )}
            </AnimatePresence>
          </div>
        </div>

        <AnimatePresence>
          {!snapshot && floorFailures > 0 && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              role="alert"
              className="flex flex-wrap items-center gap-2.5 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm font-medium text-danger-ink"
            >
              <span className="min-w-0 flex-1">{t("تعذّر تحميل حالة الصالة. بنعيد المحاولة تلقائياً…", "Couldn't load the floor. Retrying automatically…")}</span>
              <button type="button" onClick={() => void fetchFloorRef.current(true)} disabled={actionLoading} className="btn btn-secondary btn-sm" aria-busy={actionLoading}>
                {actionLoading ? <ButtonSpinner /> : <RefreshCw className="size-4" aria-hidden="true" />}
                {t("إعادة المحاولة", "Try again")}
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Content Body: Grand Floor Canvas vs Sidebar */}
        <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row">
          {/* Main Floor Canvas Section */}
          <div className="flex min-w-0 flex-1 flex-col">
            <AnimatePresence mode="wait" initial={false}>
              {viewMode === "grid" ? (
                /* Grid Mode: Cards representation */
                <motion.div key="grid" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.3, ease: EASE_OUT }} className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                  {!snapshot && Array.from({ length: 8 }, (_, index) => (
                    <div key={index} className="space-y-3 rounded-[1.5rem] border border-line bg-surface p-4" aria-hidden="true">
                      <div className="flex items-center gap-2.5"><Skeleton className="h-10 w-14 rounded-xl" /><Skeleton className="h-3.5 w-16" /></div>
                      <div className="flex justify-between border-t border-line pt-3"><Skeleton className="h-5 w-14 rounded-full" /><Skeleton className="h-3.5 w-12" /></div>
                    </div>
                  ))}
                  {snapshot && filteredTables.length === 0 && (
                    <p className="col-span-full flex flex-col items-center gap-2 rounded-2xl border border-dashed border-line-strong bg-surface-2 px-4 py-10 text-center text-sm text-muted">
                      <Search className="size-6 text-subtle" aria-hidden="true" />
                      {t("لا توجد طاولات تطابق هذا التصفية أو البحث.", "No tables match this filter or search.")}
                    </p>
                  )}
                  <AnimatePresence initial={false}>
                    {filteredTables.map((table, index) => {
                      const statusStyle = getStatusStyles(getTableView(table));
                      const currentOrders = getCurrentTableOrders(table);
                      const openOrders = getOpenTableOrders(table);
                      const isSelected = selectedTable?.table_id === table.table_id;
                      return (
                        <motion.div
                          key={table.table_id}
                          layout
                          initial={{ opacity: 0, scale: 0.94 }}
                          animate={{ opacity: 1, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.94 }}
                          transition={{ ...spring.smooth, delay: Math.min(index, 12) * 0.02 }}
                          whileHover={{ y: -3 }}
                          onClick={() => setSelectedTableId(table.table_id)}
                          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedTableId(table.table_id); } }}
                          role="button"
                          tabIndex={0}
                          aria-label={t(`تفاصيل طاولة ${table.table_number}`, `Table ${table.table_number} details`)}
                          className={`relative cursor-pointer overflow-hidden rounded-[1.5rem] border bg-surface p-4 shadow-card transition-shadow hover:shadow-lift ${isSelected ? "border-brand ring-2 ring-brand/30" : "border-line"}`}
                        >
                          <div className="mb-3 flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2.5">
                              <span className="rounded-xl bg-brand-soft px-3 py-1.5 font-display text-xl font-bold text-brand-soft-ink" dir="ltr">
                                {table.table_number}
                              </span>
                              <div>
                                <span className="block text-xs font-bold leading-tight text-ink">{TABLE_SHAPE_LABELS[table.shape][lang]}</span>
                                <span className="text-xs text-subtle">{seatsLabel(table.seat_count, lang)}</span>
                              </div>
                            </div>
                            {/* Urgent Overlays */}
                            <div className="flex items-center gap-1">
                              {currentOrders.some((order) => order.needs_attention) && <span className="flex size-6 animate-bounce items-center justify-center rounded-full bg-danger text-white" title={t("الزبون عدّل طلباً قيد التحضير", "A guest changed an order being prepared")}><Pencil className="size-3.5" aria-hidden="true" /></span>}
                              {table.overlays.has_complaint && <span className="flex size-6 animate-bounce items-center justify-center rounded-full bg-danger text-white" title={t("شكوى", "Complaint")}><TriangleAlert className="size-3.5" aria-hidden="true" /></span>}
                              {table.overlays.bill_requested && <span className="flex size-6 items-center justify-center rounded-full bg-info text-white" title={t("طلب فاتورة", "Bill requested")}><ReceiptText className="size-3.5" aria-hidden="true" /></span>}
                              {table.overlays.service_requested && <span className="flex size-6 animate-pulse items-center justify-center rounded-full bg-warning text-white" title={t("طلب نادل", "Waiter called")}><ConciergeBell className="size-3.5" aria-hidden="true" /></span>}
                            </div>
                          </div>
                          <div className="flex items-center justify-between gap-2 border-t border-line pt-3 text-xs">
                            <span className={`rounded-full px-2.5 py-1 text-[0.6875rem] font-bold ${statusStyle.pillBg}`}>
                              {statusStyle.text}
                            </span>
                            <span className="font-semibold text-muted">
                              {openOrders.length > 0
                                ? t(`${openOrders.length} طلب`, countEn(openOrders.length, "order", "orders"))
                                : currentOrders.length > 0 ? t("الطلبات مكتملة", "Orders completed") : t("لا توجد طلبات", "No orders")}
                            </span>
                          </div>
                        </motion.div>
                      );
                    })}
                  </AnimatePresence>
                </motion.div>
              ) : (
                /* Grand Isometric Canvas View */
                <motion.div
                  key="map"
                  ref={containerRef}
                  initial={{ opacity: 0, scale: 0.99 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.35, ease: EASE_OUT }}
                  className="relative flex min-h-[340px] flex-1 flex-col items-center justify-center overflow-hidden rounded-[1.75rem] border border-line bg-[#15191a] px-2 pb-2 pt-14 shadow-lift sm:min-h-[640px] sm:p-4 xl:min-h-[700px]"
                >
                  {/* Zoom and full screen */}
                  <div className="absolute start-3 top-3 z-30 flex items-center gap-1 rounded-2xl border border-white/15 bg-black/55 px-1.5 py-1.5 text-white shadow-lg backdrop-blur-md sm:start-4 sm:top-4" dir="ltr">
                    <button onClick={() => setZoomLevel((z) => Math.max(z - 0.15, 0.75))} title={t("تصغير", "Zoom out")} aria-label={t("تصغير", "Zoom out")} className="flex size-8 items-center justify-center rounded-xl transition-colors hover:bg-white/15">
                      <ZoomOut className="size-4" aria-hidden="true" />
                    </button>
                    <button onClick={() => setZoomLevel(1)} title={t("استعادة المقياس الطبيعي", "Reset zoom")} className="min-w-14 rounded-xl px-1.5 py-1 font-mono text-xs font-bold tabular-nums transition-colors hover:bg-white/15">
                      {Math.round(zoomLevel * 100)}%
                    </button>
                    <button onClick={() => setZoomLevel((z) => Math.min(z + 0.15, 1.8))} title={t("تكبير", "Zoom in")} aria-label={t("تكبير", "Zoom in")} className="flex size-8 items-center justify-center rounded-xl transition-colors hover:bg-white/15">
                      <ZoomIn className="size-4" aria-hidden="true" />
                    </button>
                    <span className="mx-0.5 h-5 w-px bg-white/20" aria-hidden="true" />
                    <button onClick={toggleFullscreen} title={t("ملء الشاشة", "Full screen")} aria-label={t("ملء الشاشة", "Full screen")} className="flex size-8 items-center justify-center rounded-xl transition-colors hover:bg-white/15">
                      <Maximize className="size-4" aria-hidden="true" />
                    </button>
                  </div>

                  {/* Legend */}
                  <div className="absolute bottom-3 start-3 z-30 hidden items-center gap-3 rounded-2xl border border-white/15 bg-black/60 px-3.5 py-2 text-xs text-white backdrop-blur-md sm:bottom-4 sm:start-4 sm:flex">
                    {legend.map((entry) => (
                      <span key={entry.label} className="flex items-center gap-1.5">
                        <span className={`size-2.5 rounded-full shadow-[0_0_8px_currentColor] ${entry.dot}`} /> {entry.label}
                      </span>
                    ))}
                  </div>

                  <AnimatePresence>
                    {!snapshot && (
                      <motion.div
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="absolute inset-x-0 bottom-3 z-30 mx-auto flex w-fit items-center gap-2 rounded-full border border-white/15 bg-black/65 px-3.5 py-2 text-xs font-semibold text-white backdrop-blur-md sm:bottom-4"
                        role="status"
                      >
                        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                        {t("جارٍ تحميل حالة الصالة…", "Loading the floor…")}
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Map stage: keeps the artwork's 1024x768 ratio. Zoom changes its real width
                    (not a visual scale), so every edge of a zoomed map can be scrolled to. */}
                <div className={`relative w-full h-full flex overflow-auto ${zoomLevel > 1 ? "" : "no-scrollbar"}`}>
                  <div
                    style={{
                      width: `${zoomLevel * 100}%`,
                      maxWidth: `${Math.round(1024 * zoomLevel)}px`,
                      containerType: "inline-size",
                      transition: "width 0.2s cubic-bezier(0.2, 0, 0, 1), max-width 0.2s cubic-bezier(0.2, 0, 0, 1)",
                    }}
                    className="@container relative m-auto aspect-[1024/768] shrink-0 rounded-xl overflow-hidden shadow-2xl select-none"
                  >
                    {/* The Authentic Architectural Isometric Blueprint Image */}
                    <Image
                      src="/floor_plan_isometric.webp"
                      alt={t("مخطط صالة مطعم جبران", "Jubran restaurant floor plan")}
                      fill
                      priority
                      sizes="(max-width: 1024px) 100vw, 1024px"
                      className="object-fill pointer-events-none"
                    />

                    {/* Table hotspots (every table the restaurant has) */}
                    {snapshot?.tables.map((table) => {
                      const isSelected = selectedTable?.table_id === table.table_id;
                      const isHovered = hoveredTable === table.table_id;
                      const statusStyle = getStatusStyles(getTableView(table));
                      const currentOrders = getCurrentTableOrders(table);
                      const openOrders = getOpenTableOrders(table);
                      // Check if matches active filter
                      const isFilteredOut = !filteredTables.some((t) => t.table_id === table.table_id);

                      return (
                        <div
                          key={table.table_id}
                          onClick={() => setSelectedTableId(table.table_id)}
                          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedTableId(table.table_id); } }}
                          role="button"
                          tabIndex={0}
                          aria-label={t(`تفاصيل طاولة ${table.table_number}`, `Table ${table.table_number} details`)}
                          onMouseEnter={() => setHoveredTable(table.table_id)}
                          onMouseLeave={() => setHoveredTable(null)}
                          style={{
                            left: `${table.x_percent}%`,
                            top: `${table.y_percent}%`,
                            transform: "translate(-50%, -50%)",
                            width: TABLE_HOTSPOT_WIDTH,
                            height: TABLE_HOTSPOT_HEIGHT,
                          }}
                          className={`absolute cursor-pointer flex items-center justify-center transition-all duration-200 group animate-in fade-in zoom-in-95 ${
                            isFilteredOut ? "opacity-25 grayscale" : "opacity-100"
                          } ${isSelected ? "z-30 scale-110" : "z-20 hover:scale-105"}`}
                        >
                          {/* 3D Isometric Table Footprint Aura & Glow */}
                          <div
                            style={{
                              boxShadow: `0 0 25px 8px ${statusStyle.glowColor}`,
                              borderColor: isSelected ? "#ffffff" : statusStyle.ringColor,
                              backgroundColor: statusStyle.glowColor,
                            }}
                            className={`absolute inset-0 rounded-2xl border-2 transition-all ${
                              isSelected
                                ? "ring-4 ring-[#233064] ring-offset-2 ring-offset-black/50 bg-white/20"
                                : "hover:bg-white/10"
                            } ${
                              table.base_state === "ORDER_PENDING" ? "animate-pulse" : ""
                            }`}
                          />

                          {/* Pulsing Concentric Radar Rings for Pending Orders */}
                          {table.base_state === "ORDER_PENDING" && (
                            <span className="absolute -inset-2 rounded-2xl animate-ping pointer-events-none" style={{ backgroundColor: "rgba(250, 204, 21, 0.48)" }} />
                          )}

                          {/* Floating Urgent Overlays Badges (Service, Bill, Complaints) */}
                          <div className="absolute -top-3 @2xl:-top-4 left-1/2 -translate-x-1/2 flex items-center gap-0.5 @2xl:gap-1 z-30 pointer-events-none scale-75 @2xl:scale-100">
                            {currentOrders.some((order) => order.needs_attention) && (
                              <span className="w-5 h-5 rounded-full bg-red-600 text-white text-[10px] font-bold flex items-center justify-center shadow-lg animate-bounce ring-1 ring-white"
                                title={t("الزبون عدّل طلباً قيد التحضير", "A guest changed an order being prepared")}>
                                ✎
                              </span>
                            )}
                            {table.overlays.has_complaint && (
                              <span className="w-5 h-5 rounded-full bg-red-600 text-white text-[10px] font-bold flex items-center justify-center shadow-lg animate-bounce ring-1 ring-white">
                                ⚠️
                              </span>
                            )}
                            {table.overlays.bill_requested && (
                              <span className="w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] font-bold flex items-center justify-center shadow-lg ring-1 ring-white">
                                🧾
                              </span>
                            )}
                            {table.overlays.service_requested && (
                              <span className="w-5 h-5 rounded-full bg-amber-500 text-white text-[10px] font-bold flex items-center justify-center shadow-lg animate-pulse ring-1 ring-white">
                                🛎️
                              </span>
                            )}
                          </div>

                          {/* Table Center 3D Badge (Table Number & Status Indicator) */}
                          <div
                            className={`relative z-10 flex flex-col items-center justify-center rounded-xl @2xl:rounded-2xl border @2xl:border-2 px-1 py-0.5 @2xl:min-w-[84px] @2xl:px-2.5 @2xl:py-2 shadow-xl ring-1 ring-black/30 transition-all ${
                              isSelected
                                ? "scale-110 border-white bg-[#233064] text-white"
                                : "border-white/80 bg-[#17203d]/95 text-white backdrop-blur-sm hover:scale-105 hover:bg-[#17203d]"
                            }`}
                          >
                            <div className="flex items-center gap-1 @2xl:gap-1.5">
                              <span className="text-[10px] @xl:text-xs @2xl:text-sm font-black tracking-tight">{table.table_number}</span>
                              <span className={`h-1.5 w-1.5 @2xl:h-2.5 @2xl:w-2.5 rounded-full ${statusStyle.dotColor} shadow-[0_0_7px_currentColor] ring-1 ring-white/70`} />
                            </div>

                            {/* Small maps show the number and colour only; the details open on tap. */}
                            <span className={`mt-1 hidden @2xl:block rounded-full px-2 py-0.5 text-[10px] font-black leading-tight shadow-sm ${statusStyle.pillBg}`}>
                              {statusStyle.text}
                            </span>

                            {/* Open orders (not yet served) */}
                            {openOrders.length > 0 && (
                              <span className="mt-1 hidden @2xl:block rounded-full bg-amber-300 px-2 py-0.5 text-[9px] font-black leading-tight text-[#201d19] shadow-sm">
                                {t(`${openOrders.length} طلب`, countEn(openOrders.length, "order", "orders"))}
                              </span>
                            )}
                          </div>

                          {/* Floating Detailed Tooltip on Hover */}
                          {isHovered && !isSelected && (
                            <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 bg-[#17203d]/95 text-white px-3 py-2 rounded-xl text-xs shadow-2xl border border-white/20 whitespace-nowrap z-40 pointer-events-none animate-in fade-in zoom-in-95 duration-150">
                              <div className="flex items-center justify-between gap-3 border-b border-white/20 pb-1 mb-1">
                                <span className="font-extrabold text-[#c5cce3]">{t(`طاولة ${table.table_number}`, `Table ${table.table_number}`)}</span>
                                <span className="text-[10px] text-gray-300">{TABLE_SHAPE_LABELS[table.shape][lang]}</span>
                              </div>
                              <div className="text-[11px] space-y-0.5">
                                <div className="flex justify-between gap-3">
                                  <span className="text-gray-300">{t("السعة:", "Seats:")}</span>
                                  <span className="font-bold">{seatsLabel(table.seat_count, lang)}</span>
                                </div>
                                <div className="flex justify-between gap-3">
                                  <span className="text-gray-300">{t("الحالة:", "Status:")}</span>
                                <span className="font-bold" style={{ color: statusStyle.ringColor }}>{statusStyle.text}</span>
                                </div>
                                {currentOrders.length > 0 && (
                                  <div className="flex justify-between gap-3 text-emerald-300 pt-0.5">
                                    <span>{t("الطلبات:", "Orders:")}</span>
                                    <span className="font-bold">{t(`${currentOrders.length} طلب في الجلسة`, countEn(currentOrders.length, "order in this visit", "orders in this visit"))}</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Operational sidebar: service requests + live incoming orders */}
          <div className="flex w-full flex-col gap-4 lg:grid lg:grid-cols-2 xl:flex xl:min-h-[700px] xl:w-[420px]">
            {/* Requests for staff, tissues, cleaning, bills, and complaints. */}
            <section className="flex flex-col rounded-[1.75rem] border border-line bg-surface p-4 shadow-card xl:min-h-0 xl:flex-1 xl:basis-0 xl:overflow-hidden" aria-label={t("طلبات الخدمات", "Service requests")}>
              <div className="mb-3 flex shrink-0 items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="flex size-9 items-center justify-center rounded-xl bg-accent-soft text-accent-ink"><ConciergeBell className="size-[18px]" aria-hidden="true" /></span>
                  <div>
                    <h2 className="font-display text-base font-bold text-ink">{t("طلبات الخدمات", "Service requests")}</h2>
                    <p className="text-xs text-muted">{t("الموظف، المناديل، التنظيف، الحساب والشكاوى", "Staff, tissues, cleaning, bill and complaints")}</p>
                  </div>
                </div>
                <span className="rounded-full bg-brand-soft px-2.5 py-1 text-xs font-bold tabular-nums text-brand-soft-ink">{filteredServiceQueue.length}</span>
              </div>

              <div className="mb-3 grid shrink-0 grid-cols-3 gap-1 rounded-2xl border border-line bg-surface-3/70 p-1" role="group" aria-label={t("تصفية طلبات الخدمات", "Filter service requests")}>
                {(["OPEN", "IN_PROGRESS", "RESOLVED"] as const).map((stage) => {
                  const active = serviceQueueFilter === stage;
                  return (
                    <button
                      key={stage}
                      type="button"
                      onClick={() => setServiceQueueFilter(stage)}
                      aria-pressed={active}
                      className={`relative isolate min-w-0 rounded-xl px-1.5 py-2 text-xs font-semibold transition-colors ${active ? "text-ink" : "text-muted hover:text-ink"}`}
                    >
                      {active && <motion.span layoutId="service-filter" className="absolute inset-0 -z-10 rounded-xl border border-line bg-surface shadow-card" transition={spring.snappy} />}
                      <span className="block leading-tight">{SERVICE_STAGE_LABELS[stage][lang]}</span>
                      <span className="mt-0.5 block font-display text-base font-bold tabular-nums">{serviceQueue.filter((item) => item.status === stage).length}</span>
                    </button>
                  );
                })}
              </div>

              <div className="max-h-[440px] space-y-2.5 overflow-y-auto pe-1 xl:max-h-none xl:min-h-0 xl:flex-1">
                <AnimatePresence initial={false} mode="popLayout">
                  {!snapshot ? (
                    <QueueSkeleton key="loading" />
                  ) : filteredServiceQueue.length === 0 ? (
                    <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-line-strong bg-surface-2 px-3 py-8 text-center text-sm text-muted">
                      <CircleCheckBig className="size-6 text-success" aria-hidden="true" />
                      {t(`لا توجد خدمات في حالة «${SERVICE_STAGE_LABELS[serviceQueueFilter].ar}» حالياً.`, `No service requests under "${SERVICE_STAGE_LABELS[serviceQueueFilter].en}" right now.`)}
                    </motion.div>
                  ) : filteredServiceQueue.map((item) => (
                    <motion.article
                      key={`${item.kind}-${item.id}`}
                      layout
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, x: 24, transition: { duration: 0.18 } }}
                      transition={spring.smooth}
                      className={`rounded-2xl border p-3.5 text-sm ${item.kind === "COMPLAINT" ? "border-danger/30 bg-danger-soft/40" : "border-line bg-surface-2"}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="rounded-lg bg-brand px-2 py-0.5 text-xs font-bold text-on-brand" dir="ltr">{item.table_number}</span>
                            <span className="font-bold text-ink">{SERVICE_TYPE_LABELS[item.type]?.[lang] ?? t("طلب خدمة", "Service request")}</span>
                          </div>
                          <p className="mt-1 select-all break-all text-[0.6875rem] text-subtle" title={t(`معرّف العميل الكامل: ${item.customer_id}`, `Full customer ID: ${item.customer_id}`)}>{t("معرّف العميل:", "Customer ID:")} {item.customer_id}</p>
                          {item.message && <p className="mt-2 whitespace-pre-wrap break-words leading-relaxed text-ink-2">{item.message}</p>}
                          {item.closure_note && <p className="mt-2 rounded-xl bg-warning-soft p-2 text-xs text-warning-ink">{item.closure_note}</p>}
                        </div>
                        <time className="shrink-0 text-xs tabular-nums text-subtle" dateTime={item.created_at}>
                          {restaurantTime(item.created_at, timeLocale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </time>
                      </div>
                      <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-line pt-2.5">
                        <span className="text-xs font-semibold text-muted">{SERVICE_STAGE_LABELS[item.status as ServiceQueueFilter]?.[lang]}</span>
                        {item.status === "OPEN" && (
                          <button type="button" disabled={actionLoading} onClick={() => item.kind === "COMPLAINT" ? handleStartComplaint(item.id) : handleStartService(item.id)} className="btn btn-sm min-h-8 rounded-lg bg-warning px-3 text-white hover:brightness-110">
                            {pendingAction === `${item.kind === "COMPLAINT" ? "complaint" : "service"}-start:${item.id}` ? <ButtonSpinner className="size-3.5" /> : <Play className="size-3.5 rtl:-scale-x-100" aria-hidden="true" />}
                            {t("بدء المعالجة", "Start handling")}
                          </button>
                        )}
                        {item.status === "IN_PROGRESS" && (
                          <button type="button" disabled={actionLoading} onClick={() => item.kind === "COMPLAINT" ? handleResolveComplaint(item.id) : handleResolveService(item.id)} className="btn btn-primary btn-sm min-h-8 rounded-lg px-3">
                            {pendingAction === `${item.kind === "COMPLAINT" ? "complaint" : "service"}-done:${item.id}` ? <ButtonSpinner /> : <Check className="size-4" aria-hidden="true" />}
                            {t("تمت الخدمة", "Done")}
                          </button>
                        )}
                      </div>
                    </motion.article>
                  ))}
                </AnimatePresence>
              </div>
              {cancelledServices.length > 0 && (
                <details className="group mt-3 border-t border-line pt-2.5 text-sm text-muted">
                  <summary className="flex cursor-pointer list-none items-center justify-between font-semibold">
                    {t(`طلبات ملغاة (${cancelledServices.length})`, `Cancelled requests (${cancelledServices.length})`)}
                    <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden="true" />
                  </summary>
                  <div className="mt-2 max-h-40 space-y-1.5 overflow-y-auto">
                    {cancelledServices.map((item) => (
                      <div key={item.id} className="flex flex-col gap-1 rounded-xl bg-surface-2 p-2.5 text-xs">
                        <div className="flex justify-between gap-2">
                          <span>{t(`طاولة ${item.table_number}`, `Table ${item.table_number}`)} · {SERVICE_TYPE_LABELS[item.type]?.[lang] ?? t("طلب خدمة", "Service request")}</span>
                          <span className="shrink-0">{item.closure_note ? t("أُلغي مع الجلسة", "Cancelled with the visit") : t("أُلغي من العميل", "Cancelled by the customer")}</span>
                        </div>
                        <span className="select-all break-all text-[0.6875rem] text-subtle">{t("معرّف العميل:", "Customer ID:")} {item.customer_id}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </section>

            {/* Global Incoming Orders Queue */}
            <section className="flex flex-col rounded-[1.75rem] border border-line bg-surface p-4 shadow-card xl:min-h-0 xl:flex-1 xl:basis-0 xl:overflow-hidden" aria-label={t("الطلبات الواردة للصالة", "Incoming floor orders")}>
              <div className="mb-3 flex shrink-0 items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="flex size-9 items-center justify-center rounded-xl bg-brand-soft text-brand-soft-ink"><ReceiptText className="size-[18px]" aria-hidden="true" /></span>
                  <h2 className="font-display text-base font-bold text-ink">{t("الطلبات الواردة للصالة", "Incoming floor orders")}</h2>
                  <span className="rounded-full bg-brand-soft px-2.5 py-1 text-xs font-bold tabular-nums text-brand-soft-ink">{queueOrders.length}</span>
                </div>
                <span className="flex items-center gap-1.5 text-xs text-subtle"><Radio className="size-3.5 text-success" aria-hidden="true" />{t("تحديث فوري", "Live updates")}</span>
              </div>

              <div className="mb-3 grid shrink-0 grid-cols-4 gap-1 rounded-2xl border border-line bg-surface-3/70 p-1" role="group" aria-label={t("تصفية الطلبات حسب الحالة", "Filter orders by status")}>
                {(["PENDING_APPROVAL", "PREPARING", "READY", "DELIVERED"] as const).map((stage) => {
                  const active = orderQueueFilter === stage;
                  return (
                    <button
                      key={stage}
                      type="button"
                      onClick={() => setOrderQueueFilter(stage)}
                      aria-pressed={active}
                      className={`relative isolate min-w-0 rounded-xl px-1 py-2 text-[0.6875rem] font-semibold transition-colors ${active ? "text-ink" : "text-muted hover:text-ink"}`}
                    >
                      {active && <motion.span layoutId="orders-filter" className="absolute inset-0 -z-10 rounded-xl border border-line bg-surface shadow-card" transition={spring.snappy} />}
                      <span className="block leading-tight">{ORDER_STAGE_LABELS[stage][lang]}</span>
                      <span className={`mt-0.5 block font-display text-base font-bold tabular-nums ${active ? "text-brand" : ""}`}>{queueCounts[stage]}</span>
                    </button>
                  );
                })}
              </div>

              <div className="max-h-[480px] flex-1 space-y-2.5 overflow-y-auto pe-1 xl:max-h-none xl:min-h-0">
                <AnimatePresence initial={false} mode="popLayout">
                  {!snapshot ? (
                    <QueueSkeleton key="loading" />
                  ) : queueOrders.length === 0 ? (
                    <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex min-h-[82px] flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-line-strong bg-surface-2 px-4 py-6 text-center text-sm text-muted">
                      <Coffee className="size-6 text-brand/60" aria-hidden="true" />
                      {t(`لا توجد طلبات في حالة «${ORDER_STAGE_LABELS[orderQueueFilter].ar}» حالياً.`, `No orders under "${ORDER_STAGE_LABELS[orderQueueFilter].en}" right now.`)}
                    </motion.div>
                  ) : (
                    queueOrders.map((ord) => (
                      <IncomingOrderCard
                        key={ord.order_id}
                        ord={ord}
                        busy={actionLoading}
                        pending={pendingAction}
                        onStartPreparing={(orderId) => void handleStartPreparing(orderId)}
                        onMarkReady={(orderId) => void handleMarkReady(orderId)}
                        onMarkServed={(orderId) => void handleMarkServed(orderId)}
                        onAcknowledge={(orderId) => void handleAcknowledgeChanges(orderId)}
                      />
                    ))
                  )}
                </AnimatePresence>
              </div>
            </section>
          </div>
        </div>
      </main>

      {/* Table details open above the floor without replacing the service queue. */}
      <Sheet open={Boolean(selectedTable)} onClose={() => setSelectedTableId(null)} labelledBy="table-details-title" size="lg">
        {selectedTable && (
          <>
            <div className="flex shrink-0 items-start justify-between gap-3 px-5 pb-3 pt-2 sm:px-6 sm:pt-6">
              <div className="flex items-center gap-3">
                <span className="rounded-2xl bg-brand px-3.5 py-2 font-display text-2xl font-bold text-on-brand shadow-glow" dir="ltr">
                  {selectedTable.table_number}
                </span>
                <div>
                  <h2 id="table-details-title" className="font-display text-xl font-bold text-ink">
                    {t(`طاولة ${selectedTable.table_number}`, `Table ${selectedTable.table_number}`)}
                  </h2>
                  <p className="text-sm text-muted">
                    {seatsLabel(selectedTable.seat_count, lang)} • {TABLE_SHAPE_LABELS[selectedTable.shape][lang]}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setSelectedTableId(null)}
                className="flex size-10 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-3 hover:text-ink"
                title={t("إغلاق النافذة", "Close")}
                aria-label={t("إغلاق تفاصيل الطاولة", "Close table details")}
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>

            <SheetBody className="space-y-4" >
              <TableGuests
                orders={currentTableOrders}
                services={currentTableServices}
                complaints={currentTableComplaints}
                onOpenHistory={(customerId) => void handleLookupCustomerHistory(customerId)}
                historyLoadingFor={historyLoadingFor}
              />

              {/* Table state and the visit's requests */}
              <section className="space-y-3 rounded-2xl border border-line bg-surface-2 p-4 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-muted">{t("الحالة التشغيلية:", "Current status:")}</span>
                  <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${getStatusStyles(getTableView(selectedTable)).pillBg}`}>
                    {getStatusStyles(getTableView(selectedTable)).text}
                  </span>
                </div>
                {selectedTable.active_session_id && (
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-muted">{t("الجلسة النشطة:", "Active visit:")}</span>
                    <span className="font-mono font-bold text-brand" dir="ltr">{selectedTable.active_session_id.substring(0, 8)}...</span>
                  </div>
                )}

                <div className="space-y-2 border-t border-line pt-3">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-warning-ink">
                    <ConciergeBell className="size-4" aria-hidden="true" />
                    {t(`خدمات الجلسة الحالية (${currentTableServices.length}):`, `Service requests this visit (${currentTableServices.length}):`)}
                  </span>
                  {currentTableServices.map((sr) => (
                    <div key={sr.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-xl border p-2.5 text-xs ${sr.status === "OPEN" ? "border-warning/30 bg-warning-soft" : sr.status === "IN_PROGRESS" ? "border-preparing/30 bg-preparing-soft" : sr.status === "CANCELLED" ? "border-line bg-surface-3" : "border-success/30 bg-success-soft"}`}>
                      <span className="font-semibold text-ink">
                        {({ STAFF: t("طلب موظف", "Staff call"), TISSUES: t("مناديل إضافية", "Extra tissues"), CLEAN_TABLE: t("تنظيف الطاولة", "Clean the table"), BILL: t("طلب الحساب", "Bill request") } as Record<string, string>)[sr.type] ?? t("طلب خدمة", "Service request")}
                      </span>
                      <span className="select-all break-all text-[0.6875rem] text-subtle" title={t(`معرّف العميل الكامل: ${sr.customer_id}`, `Full customer ID: ${sr.customer_id}`)}>{t("عميل:", "Customer:")} {sr.customer_id}</span>
                      <div className="flex items-center gap-2">
                        <span className="whitespace-nowrap text-[0.6875rem] font-bold text-ink-2">
                          {sr.status === "OPEN" ? t("طلبات جديدة", "New") : sr.status === "IN_PROGRESS" ? t("قيد التنفيذ", "In progress") : sr.status === "CANCELLED" ? (sr.closure_note ? t("أُلغي بسبب إنهاء الجلسة", "Cancelled because the visit ended") : t("طُلب ثم أُلغي من العميل", "Requested, then cancelled by the customer")) : t("مكتملة", "Completed")}
                        </span>
                        <time className="whitespace-nowrap text-[0.6875rem] text-subtle" dateTime={sr.created_at}>
                          {restaurantTime(sr.created_at, timeLocale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </time>
                        {sr.closure_note && <span className="max-w-48 text-[0.6875rem] text-warning-ink">{sr.closure_note}</span>}
                        {sr.status === "OPEN" && (
                          <button onClick={() => handleStartService(sr.id)} disabled={actionLoading} className="inline-flex items-center gap-1 rounded-lg bg-warning px-2.5 py-1 text-[0.6875rem] font-bold text-white hover:brightness-110 disabled:opacity-50">
                            {pendingAction === `service-start:${sr.id}` && <ButtonSpinner className="size-3" />}
                            {t("بدء المعالجة", "Start handling")}
                          </button>
                        )}
                        {sr.status === "IN_PROGRESS" && (
                          <button onClick={() => handleResolveService(sr.id)} disabled={actionLoading} className="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-[0.6875rem] font-bold text-on-brand hover:bg-brand-hover disabled:opacity-50">{pendingAction === `service-done:${sr.id}` && <ButtonSpinner className="size-3" />}{t("تمت الخدمة ✓", "Done ✓")}</button>
                        )}
                      </div>
                    </div>
                  ))}
                  {currentTableServices.length === 0 && (
                    <p className="rounded-xl bg-surface px-3 py-2 text-xs text-subtle">{t("لا توجد طلبات خدمات على هذه الطاولة.", "No service requests at this table.")}</p>
                  )}
                </div>

                <div className="space-y-2 border-t border-line pt-3">
                  <span className="flex items-center gap-1.5 text-xs font-bold text-danger-ink">
                    <TriangleAlert className="size-4" aria-hidden="true" />
                    {t(`شكاوى الجلسة الحالية (${currentTableComplaints.length}):`, `Complaints this visit (${currentTableComplaints.length}):`)}
                  </span>
                  {currentTableComplaints.map((c) => (
                    <div key={c.id} className={`flex items-start justify-between gap-2 rounded-xl border p-2.5 text-xs ${c.status === "CANCELLED" ? "border-line bg-surface-3" : "border-danger/30 bg-danger-soft"}`}>
                      <div className="min-w-0">
                        <p className={`break-words font-semibold ${c.status === "CANCELLED" ? "text-ink-2" : "text-danger-ink"}`}>{c.message}</p>
                        <p className="mt-1 select-all break-all text-[0.6875rem] text-subtle" title={t(`معرّف العميل الكامل: ${c.customer_id}`, `Full customer ID: ${c.customer_id}`)}>{t("معرّف العميل:", "Customer ID:")} {c.customer_id}</p>
                        <time className="mt-1 block text-[0.6875rem] text-subtle" dateTime={c.created_at}>
                          {restaurantTime(c.created_at, timeLocale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </time>
                        {c.closure_note && <p className="mt-1 text-[0.6875rem] text-warning-ink">{c.closure_note}</p>}
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <span className="text-[0.6875rem] font-bold text-danger-ink">{c.status === "OPEN" ? t("طلبات جديدة", "New") : c.status === "IN_PROGRESS" ? t("قيد التنفيذ", "In progress") : c.status === "CANCELLED" ? t("أُلغيت بسبب إنهاء الجلسة", "Cancelled because the visit ended") : t("مكتملة", "Completed")}</span>
                        {c.status === "OPEN" && <button onClick={() => handleStartComplaint(c.id)} disabled={actionLoading} className="inline-flex items-center gap-1 rounded-lg bg-warning px-2.5 py-1 text-[0.6875rem] font-bold text-white disabled:opacity-50">{pendingAction === `complaint-start:${c.id}` && <ButtonSpinner className="size-3" />}{t("بدء المعالجة", "Start handling")}</button>}
                        {c.status === "IN_PROGRESS" && <button onClick={() => handleResolveComplaint(c.id)} disabled={actionLoading} className="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-[0.6875rem] font-bold text-on-brand disabled:opacity-50">{pendingAction === `complaint-done:${c.id}` && <ButtonSpinner className="size-3" />}{t("تمت الخدمة ✓", "Done ✓")}</button>}
                      </div>
                    </div>
                  ))}
                  {currentTableComplaints.length === 0 && (
                    <p className="rounded-xl bg-surface px-3 py-2 text-xs text-subtle">{t("لا توجد شكاوى على هذه الطاولة.", "No complaints at this table.")}</p>
                  )}
                </div>
              </section>

              {/* Table Orders in this session */}
              <section>
                <h3 className="mb-2 text-sm font-bold text-ink">
                  {t(`طلبات الجلسة الحالية وحالاتها (${currentTableOrders.length})`, `Orders this visit and their status (${currentTableOrders.length})`)}
                </h3>
                {currentTableOrders.length > 0 ? (
                  <div className="space-y-2.5">
                    {currentTableOrders.map((ord) => (
                      <div key={ord.order_id} className={`space-y-2 rounded-2xl border p-3 text-sm ${ord.needs_attention ? "border-danger/50 bg-danger-soft/40" : "border-line bg-surface-2"}`}>
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono font-bold text-brand" dir="ltr">{ord.order_number}</span>
                            <span className="select-all break-all text-[0.6875rem] text-subtle" title={t(`معرّف العميل الكامل: ${ord.customer_id}`, `Full customer ID: ${ord.customer_id}`)}>{t("عميل:", "Customer:")} {ord.customer_id}</span>
                            <time className="text-[0.6875rem] text-subtle" dateTime={ord.created_at}>
                              {restaurantTime(ord.created_at, timeLocale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                            </time>
                          </div>
                          <StatusPill size="sm" dot tone={getOrderStage(ord) === "PENDING_APPROVAL" ? "warning" : getOrderStage(ord) === "PREPARING" ? "preparing" : "success"} pulse={getOrderStage(ord) === "PENDING_APPROVAL"}>
                            {getOrderStageLabel(ord, t)}
                          </StatusPill>
                        </div>
                        <div className="space-y-1 text-[0.8125rem] text-ink-2">
                          {ord.items.map((it, i) => (
                            <div key={i} className="flex items-center justify-between gap-2">
                              <span>
                                <span className="font-bold tabular-nums text-ink">{it.quantity}×</span> {localized(lang, it.name_ar, it.name_en)}
                                <AddedLaterTag show={it.added_later} />
                              </span>
                              <span className="font-medium tabular-nums">{localized(lang, it.line_total_display, it.line_total_display_en)}</span>
                            </div>
                          ))}
                        </div>
                        <OrderChangeNotice order={ord} onAcknowledge={(orderId) => void handleAcknowledgeChanges(orderId)} busy={actionLoading} pending={pendingAction === `seen:${ord.order_id}`} />
                        {ord.closure_note && !ord.cancelled_by_guest && <p className="rounded-xl bg-warning-soft p-2 text-xs text-warning-ink">{ord.closure_note}</p>}
                        <div className="flex items-center justify-between gap-2 border-t border-line pt-2">
                          <span className="font-display text-base font-bold tabular-nums text-brand">{localized(lang, ord.total_display, ord.total_display_en)}</span>
                          {/* Direct Stage Transitions for this table's order */}
                          <div className="flex items-center gap-1.5">
                            {ord.status === "PENDING_APPROVAL" && (
                              <button onClick={() => handleStartPreparing(ord.order_id)} disabled={actionLoading} className="btn btn-sm min-h-8 rounded-lg bg-preparing px-3 text-white hover:brightness-110">
                                {pendingAction === `prepare:${ord.order_id}` ? <ButtonSpinner /> : <ChefHat className="size-4" aria-hidden="true" />}{t("بدء التحضير", "Start preparing")}
                              </button>
                            )}
                            {ord.status === "PREPARING" && (
                              <button onClick={() => handleMarkReady(ord.order_id)} disabled={actionLoading} className="btn btn-sm min-h-8 rounded-lg bg-success px-3 text-white hover:brightness-110">
                                {pendingAction === `ready:${ord.order_id}` ? <ButtonSpinner /> : <BellRing className="size-4" aria-hidden="true" />}{t("جاهز", "Ready")}
                              </button>
                            )}
                            {ord.status === "READY" && !ord.is_served && (
                              <button onClick={() => handleMarkServed(ord.order_id)} disabled={actionLoading} className="btn btn-primary btn-sm min-h-8 rounded-lg px-3">
                                {pendingAction === `served:${ord.order_id}` ? <ButtonSpinner /> : <HandPlatter className="size-4" aria-hidden="true" />}{t("تم التقديم", "Served")}
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-line-strong bg-surface-2 py-5 text-center text-sm text-muted">
                    {t("لا توجد طلبات مسجلة على هذه الطاولة حالياً.", "No orders at this table right now.")}
                  </div>
                )}
              </section>
            </SheetBody>

            {/* Close Session Button */}
            {selectedTable.active_session_id && (
              <SheetFooter>
                <button
                  onClick={() => handleCloseSession(selectedTable.active_session_id!, selectedTable.table_number)}
                  disabled={actionLoading}
                  aria-busy={pendingAction === `close:${selectedTable.active_session_id}`}
                  className="btn btn-danger-soft w-full"
                >
                  {pendingAction === `close:${selectedTable.active_session_id}` ? <ButtonSpinner className="size-[18px]" /> : <DoorOpen className="size-[18px] rtl:-scale-x-100" aria-hidden="true" />}
                  {t(`إنهاء وإغلاق جلسة طاولة ${selectedTable.table_number}`, `End and close the visit at table ${selectedTable.table_number}`)}
                </button>
              </SheetFooter>
            )}
          </>
        )}
      </Sheet>

      {/* One customer's whole visit */}
      <Sheet open={Boolean(customerHistory)} onClose={() => setCustomerHistory(null)} labelledBy="customer-history-title" size="lg">
        {customerHistory && (
          <>
            <SheetHeader
              id="customer-history-title"
              icon={<History className="size-6" aria-hidden="true" />}
              title={t(`سجل العميل · طاولة ${customerHistory.table_number}`, `Customer history · Table ${customerHistory.table_number}`)}
              subtitle={
                <>
                  <span className="block select-all break-all font-mono text-xs">ID: {customerHistory.customer_id}</span>
                  <span className="mt-1 block text-xs">{t("الجلسة:", "Visit:")} {customerHistory.session_status === "ACTIVE" ? t("نشطة", "active") : t("منتهية", "closed")} · {t("بدأت", "started")} {restaurantTime(customerHistory.session_started_at, timeLocale)}{customerHistory.session_closed_at ? ` · ${t("انتهت", "ended")} ${restaurantTime(customerHistory.session_closed_at, timeLocale)}` : ""}</span>
                </>
              }
              onClose={() => setCustomerHistory(null)}
            />
            <SheetBody className="space-y-5 text-sm">
              <section>
                <h3 className="mb-2 font-bold text-ink">{t(`طلبات الطعام (${customerHistory.orders.length})`, `Food orders (${customerHistory.orders.length})`)}</h3>
                {customerHistory.orders.length ? customerHistory.orders.map((order) => (
                  <article key={order.order_id} className="mb-2 rounded-2xl border border-line bg-surface-2 p-3">
                    <div className="flex justify-between gap-2 font-bold text-ink"><span className="font-mono" dir="ltr">{order.order_number}</span><span className="text-xs">{order.status === "CANCELLED" ? t("أُلغي مع الجلسة", "Cancelled with the visit") : order.status === "PENDING_APPROVAL" ? t("بانتظار التأكيد", "Awaiting confirmation") : order.status === "PREPARING" ? t("قيد التحضير", "Preparing") : order.status === "READY" ? (order.is_served ? t("جاهز · تم التقديم", "Ready · served") : t("جاهز", "Ready")) : t("أُغلق مع الجلسة", "Closed with the visit")}</span></div>
                    <p className="mt-1 text-muted">{order.items.map((item) => `${item.quantity}× ${localized(lang, item.name_ar, item.name_en)}`).join(t("، ", ", "))} · {localized(lang, order.total_display, order.total_display_en)}</p>
                    {order.closure_note && <p className="mt-2 rounded-xl bg-warning-soft p-2 text-xs text-warning-ink">{order.closure_note}</p>}
                    <time className="mt-1 block text-xs text-subtle">{restaurantTime(order.created_at, timeLocale)}</time>
                  </article>
                )) : <p className="rounded-xl bg-surface-2 p-3 text-subtle">{t("لا توجد طلبات طعام مسجلة.", "No food orders recorded.")}</p>}
              </section>
              <section>
                <h3 className="mb-2 font-bold text-ink">{t(`الخدمات والشكاوى (${customerHistory.services.length + customerHistory.complaints.length})`, `Service requests and complaints (${customerHistory.services.length + customerHistory.complaints.length})`)}</h3>
                {[...customerHistory.services.map((service) => ({ id: service.id, label: SERVICE_TYPE_LABELS[service.type]?.[lang] ?? service.type, status: service.status, created_at: service.created_at, message: "", closure_note: service.closure_note })), ...customerHistory.complaints.map((complaint) => ({ id: complaint.id, label: t("شكوى", "Complaint"), status: complaint.status, created_at: complaint.created_at, message: complaint.message, closure_note: complaint.closure_note }))].length ? (
                  [...customerHistory.services.map((service) => ({ id: service.id, label: SERVICE_TYPE_LABELS[service.type]?.[lang] ?? service.type, status: service.status, created_at: service.created_at, message: "", closure_note: service.closure_note })), ...customerHistory.complaints.map((complaint) => ({ id: complaint.id, label: t("شكوى", "Complaint"), status: complaint.status, created_at: complaint.created_at, message: complaint.message, closure_note: complaint.closure_note }))].map((item) => (
                    <article key={item.id} className="mb-2 flex items-start justify-between gap-3 rounded-2xl border border-line p-3">
                      <div><p className="font-bold text-ink">{item.label}</p>{item.message && <p className="mt-1 text-muted">{item.message}</p>}{item.closure_note && <p className="mt-2 rounded-xl bg-warning-soft p-2 text-xs text-warning-ink">{item.closure_note}</p>}<time className="mt-1 block text-xs text-subtle">{restaurantTime(item.created_at, timeLocale)}</time></div>
                      <span className="shrink-0 text-xs font-bold text-ink-2">{item.status === "CANCELLED" ? t("ملغى", "Cancelled") : item.status === "RESOLVED" ? t("مكتمل", "Completed") : item.status === "IN_PROGRESS" ? t("قيد التنفيذ", "In progress") : t("جديد", "New")}</span>
                    </article>
                  ))
                ) : <p className="rounded-xl bg-surface-2 p-3 text-subtle">{t("لا توجد خدمات أو شكاوى مسجلة.", "No service requests or complaints recorded.")}</p>}
              </section>
              {customerHistory.drafts.length > 0 && <section><h3 className="mb-2 font-bold text-ink">{t(`المسودات (${customerHistory.drafts.length})`, `Drafts (${customerHistory.drafts.length})`)}</h3>{customerHistory.drafts.map((draft) => <p key={draft.id} className="mb-1 rounded-xl bg-surface-2 p-2.5">{draft.status === "ABANDONED" ? t("أُلغيت مع إنهاء الجلسة", "Cancelled when the visit ended") : draft.status} · {restaurantTime(draft.created_at, timeLocale)}</p>)}</section>}
            </SheetBody>
          </>
        )}
      </Sheet>
    </div>
  );
}
