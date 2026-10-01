"use client";

import { useLanguage } from "@/context/LanguageContext";
import { formatFils } from "@/lib/price";
import { ButtonSpinner } from "@/components/ui/Feedback";

interface GuestOrder {
  order_id: string;
  order_number: string;
  customer_id: string;
  status: string;
  is_served: boolean;
  created_at: string;
  total_minor?: number;
  items: Array<{ name_ar: string; name_en?: string; quantity: number; added_later?: boolean }>;
  amendments?: unknown[];
  needs_attention?: boolean;
  cancelled_by_guest?: boolean;
}

interface GuestRequest {
  id: string;
  customer_id: string;
  status: string;
  created_at: string;
  type?: string;
  message?: string;
}

const ORDER_STATE: Record<string, { ar: string; en: string }> = {
  PENDING_APPROVAL: { ar: "بانتظار القبول", en: "Awaiting approval" },
  PREPARING: { ar: "قيد التحضير", en: "Preparing" },
  READY: { ar: "جاهز", en: "Ready" },
  SERVED: { ar: "تسلّم", en: "Served" },
  CANCELLED: { ar: "ملغي", en: "Cancelled" },
  CLOSED: { ar: "مغلق", en: "Closed" },
};

const SERVICE_NAMES: Record<string, { ar: string; en: string }> = {
  STAFF: { ar: "طلب موظف", en: "Staff call" },
  TISSUES: { ar: "مناديل", en: "Tissues" },
  CLEAN_TABLE: { ar: "تنظيف الطاولة", en: "Table cleaning" },
  BILL: { ar: "طلب الحساب", en: "Bill" },
};

function orderState(order: GuestOrder): string {
  if (order.status === "READY" && order.is_served) return "SERVED";
  return order.status;
}

function isDone(order: GuestOrder): boolean {
  return ["SERVED", "CANCELLED", "CLOSED"].includes(orderState(order));
}

/**
 * Everyone at the table, one block per guest: what they ordered (finished or still
 * in progress, and changes they made), what they asked for, and their bill.
 */
export function TableGuests({ orders, services, complaints, onOpenHistory, historyLoadingFor = null }: {
  orders: GuestOrder[];
  services: GuestRequest[];
  complaints: GuestRequest[];
  onOpenHistory?: (customerId: string) => void;
  /** The guest whose history is being opened right now. */
  historyLoadingFor?: string | null;
}) {
  const { lang, t } = useLanguage();
  const money = (fils: number) => formatFils(fils, lang);
  const firstSeen = new Map<string, string>();
  for (const moment of [...orders, ...services, ...complaints]) {
    const current = firstSeen.get(moment.customer_id);
    if (!current || moment.created_at < current) firstSeen.set(moment.customer_id, moment.created_at);
  }
  const guests = [...firstSeen.entries()].sort((a, b) => a[1].localeCompare(b[1])).map(([id]) => id);
  if (guests.length === 0) return null;

  const billOf = (customerId: string) => orders
    .filter((order) => order.customer_id === customerId && order.status !== "CANCELLED")
    .reduce((sum, order) => sum + (order.total_minor ?? 0), 0);
  const tableTotal = guests.reduce((sum, id) => sum + billOf(id), 0);

  return (
    <section className="mb-3 rounded-xl border border-brand-line bg-brand-soft/40 p-3" aria-labelledby="table-guests-title">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 id="table-guests-title" className="text-xs font-black text-brand">
          {t(`الزبائن على الطاولة (${guests.length})`, `Guests at this table (${guests.length})`)}
        </h3>
        <span className="rounded-full bg-brand px-2.5 py-0.5 text-xs font-bold text-white">
          {t("مجموع الطاولة", "Table total")}: {money(tableTotal)}
        </span>
      </div>
      <div className="space-y-2">
        {guests.map((customerId, index) => {
          const guestOrders = orders.filter((order) => order.customer_id === customerId)
            .sort((a, b) => a.created_at.localeCompare(b.created_at));
          const guestRequests = services.filter((request) => request.customer_id === customerId);
          const guestComplaints = complaints.filter((complaint) => complaint.customer_id === customerId);
          const open = guestOrders.filter((order) => !isDone(order)).length;
          return (
            <article key={customerId} className="rounded-lg border border-line bg-surface p-2.5 text-xs">
              <header className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="font-black text-ink">{t(`ضيف ${index + 1}`, `Guest ${index + 1}`)}</span>
                  <button type="button" onClick={() => onOpenHistory?.(customerId)} title={customerId} aria-busy={historyLoadingFor === customerId}
                    className="inline-flex min-w-0 items-center gap-1 font-mono text-[0.625rem] text-subtle underline-offset-2 hover:underline">
                    {historyLoadingFor === customerId && <ButtonSpinner className="size-3 text-brand" />}
                    <span className="truncate">{customerId.slice(0, 8)}</span>
                  </button>
                  {open > 0
                    ? <span className="rounded bg-preparing-soft px-1.5 py-0.5 text-[0.625rem] font-bold text-preparing-ink">{t(`${open} قيد التنفيذ`, `${open} in progress`)}</span>
                    : guestOrders.length > 0 && <span className="rounded bg-success-soft px-1.5 py-0.5 text-[0.625rem] font-bold text-success-ink">{t("كل طلباته اكتملت", "All orders done")}</span>}
                </div>
                <span className="font-bold text-brand">{t("الحساب", "Bill")}: {money(billOf(customerId))}</span>
              </header>
              {guestOrders.length === 0 && <p className="text-[0.6875rem] text-subtle">{t("لم يطلب بعد.", "No orders yet.")}</p>}
              <ul className="space-y-1">
                {guestOrders.map((order, orderIndex) => {
                  const state = orderState(order);
                  const label = ORDER_STATE[state] ?? { ar: state, en: state };
                  return (
                    <li key={order.order_id} className={`rounded-md px-2 py-1 ${order.needs_attention ? "bg-danger-soft ring-1 ring-danger/50" : isDone(order) ? "bg-surface-3" : "bg-accent-soft/60"}`}>
                      <div className="flex flex-wrap items-center justify-between gap-1">
                        <span className="font-bold text-ink">
                          <span aria-hidden="true">{isDone(order) ? "✓ " : "⏳ "}</span>
                          {order.order_number}
                          {orderIndex > 0 && <span className="ms-1 text-[0.625rem] font-semibold text-subtle">{t("(طلب إضافي)", "(extra order)")}</span>}
                        </span>
                        <span className="flex items-center gap-1.5">
                          {(order.amendments?.length ?? 0) > 0 && !order.cancelled_by_guest && (
                            <span className={`rounded px-1 text-[0.625rem] font-bold ${order.needs_attention ? "bg-danger text-white" : "bg-info-soft text-info-ink"}`}>{t("معدّل", "changed")}</span>
                          )}
                          <span className="text-[0.6875rem] text-muted">{t(label.ar, label.en)}</span>
                          <span className="font-semibold">{order.status === "CANCELLED" ? "—" : money(order.total_minor ?? 0)}</span>
                        </span>
                      </div>
                      <p className="mt-0.5 text-[0.6875rem] text-muted">
                        {order.items.map((item) => `${item.quantity}× ${lang === "en" && item.name_en ? item.name_en : item.name_ar}${item.added_later ? t(" (مُضاف)", " (added)") : ""}`).join(t("، ", ", "))}
                      </p>
                    </li>
                  );
                })}
              </ul>
              {(guestRequests.length > 0 || guestComplaints.length > 0) && (
                <p className="mt-1.5 text-[0.6875rem] text-muted">
                  {[...guestRequests.map((request) => {
                    const name = SERVICE_NAMES[request.type ?? ""] ?? { ar: "طلب خدمة", en: "Service" };
                    const done = request.status === "RESOLVED" || request.status === "CANCELLED";
                    return `${done ? "✓" : "⏳"} ${t(name.ar, name.en)}`;
                  }), ...guestComplaints.map((complaint) => `${complaint.status === "RESOLVED" ? "✓" : "⚠"} ${t("شكوى", "Complaint")}`)]
                    .join(" · ")}
                </p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
