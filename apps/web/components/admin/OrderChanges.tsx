"use client";

import { useLanguage } from "@/context/LanguageContext";
import { ButtonSpinner } from "@/components/ui/Feedback";
import { restaurantTime } from "@/lib/time";
import { unitLabel, type LineUnit } from "@/lib/units";

export interface OrderChange extends LineUnit {
  type: "added" | "removed" | "quantity_changed" | "note_changed" | "unit_changed";
  name_ar: string;
  name_en?: string;
  quantity_before: number;
  quantity_after: number;
  note?: string | null;
  /** note_changed: the note before and after (none: no note). */
  note_before?: string | null;
  note_after?: string | null;
  /** unit_changed: how it was served before. */
  unit_before?: string | null;
  unit_before_display_ar?: string | null;
  unit_before_display_en?: string | null;
}

export interface OrderAmendment {
  id: string;
  created_at: string;
  order_status: string;
  changes: OrderChange[];
  total_after_display: string;
  total_after_display_en?: string;
  acknowledged: boolean;
  needs_attention: boolean;
}

export interface ChangeableOrder {
  order_id: string;
  status: string;
  amendments?: OrderAmendment[];
  needs_attention?: boolean;
  cancelled_by_guest?: boolean;
}

function useChangeText() {
  const { lang, t } = useLanguage();
  return (change: OrderChange) => {
    const unit = unitLabel(change, lang);
    const dish = lang === "en" && change.name_en ? change.name_en : change.name_ar;
    const name = dish + (unit ? ` · ${unit}` : "");
    const note = change.note ? ` («${change.note}»)` : "";
    if (change.type === "added") return t(`+ أضاف ${change.quantity_after} × ${name}${note}`, `+ added ${change.quantity_after} × ${name}${note}`);
    if (change.type === "removed") return t(`− حذف ${name}${note} (${change.quantity_before})`, `− removed ${name}${note} (${change.quantity_before})`);
    if (change.type === "note_changed") {
      return change.note_after
        ? t(`${change.quantity_after} × ${name}: صار «${change.note_after}»`, `${change.quantity_after} × ${name}: now «${change.note_after}»`)
        : t(`${change.quantity_after} × ${name}: شال الملاحظة «${change.note_before ?? ""}»`, `${change.quantity_after} × ${name}: note «${change.note_before ?? ""}» removed`);
    }
    if (change.type === "unit_changed") {
      const before = (lang === "en" ? change.unit_before_display_en : change.unit_before_display_ar) || change.unit_before || "";
      return t(`${change.quantity_after} × ${dish}: ${before} ← ${unit ?? ""}`, `${change.quantity_after} × ${dish}: ${before} → ${unit ?? ""}`);
    }
    return t(`${name}: ${change.quantity_before} ← ${change.quantity_after}`, `${name}: ${change.quantity_before} → ${change.quantity_after}`);
  };
}

/**
 * What the guest changed after sending the order.
 * - Changed while the kitchen was preparing it: a loud banner until staff press "Seen".
 * - Changed before the restaurant accepted it: just a quiet note.
 */
export function OrderChangeNotice({ order, onAcknowledge, busy, pending = false }: {
  order: ChangeableOrder;
  onAcknowledge: (orderId: string) => void;
  busy?: boolean;
  /** "Seen" for this order is on its way. */
  pending?: boolean;
}) {
  const { lang, t } = useLanguage();
  const changeText = useChangeText();
  const amendments = order.amendments ?? [];
  if (order.cancelled_by_guest) {
    return <p className="mb-2 rounded-lg border border-line bg-surface-3 px-2 py-1.5 text-[0.6875rem] font-bold text-ink-2">{t("ألغى الزبون هذا الطلب قبل التسليم.", "The guest cancelled this order before it was served.")}</p>;
  }
  if (amendments.length === 0) return null;
  const unseen = amendments.filter((amendment) => amendment.needs_attention);
  if (order.needs_attention && unseen.length > 0) {
    return (
      <div role="alert" className="mb-2 rounded-xl border-2 border-danger bg-danger-soft p-2.5 text-xs text-danger-ink shadow-[0_0_0_3px_color-mix(in_oklab,var(--danger)_18%,transparent)]">
        <div className="mb-1 flex items-center gap-1.5 font-black">
          <span className="relative flex h-2.5 w-2.5" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-danger" />
          </span>
          {t("الزبون عدّل الطلب أثناء التحضير", "The guest changed this order while it was being prepared")}
        </div>
        <ul className="mb-2 space-y-0.5 font-semibold">
          {unseen.flatMap((amendment) => amendment.changes.map((change, index) => (
            <li key={`${amendment.id}-${index}`}>{changeText(change)}</li>
          )))}
        </ul>
        <button type="button" onClick={() => onAcknowledge(order.order_id)} disabled={busy} aria-busy={pending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-danger px-3 py-1 text-xs font-bold text-white hover:brightness-110 disabled:opacity-60">
          {pending && <ButtonSpinner className="size-3.5" />}
          {t("تم الاطلاع", "Seen")}
        </button>
      </div>
    );
  }
  const last = amendments[amendments.length - 1];
  return (
    <details className="mb-2 rounded-lg bg-surface-3 px-2 py-1 text-[0.6875rem] text-muted">
      <summary className="cursor-pointer font-semibold">
        {t("عدّل الزبون الطلب", "Changed by the guest")} · {restaurantTime(last.created_at, lang === "ar" ? "ar-JO" : "en-GB", { hour: "2-digit", minute: "2-digit" })}
      </summary>
      <ul className="mt-1 space-y-0.5">
        {amendments.flatMap((amendment) => amendment.changes.map((change, index) => (
          <li key={`${amendment.id}-${index}`}>{changeText(change)}</li>
        )))}
      </ul>
    </details>
  );
}

/** Marks a dish the guest added after sending the order. */
export function AddedLaterTag({ show }: { show?: boolean }) {
  const { t } = useLanguage();
  if (!show) return null;
  return <span className="ms-1 rounded bg-accent-soft px-1 py-0.5 text-[0.625rem] font-bold text-accent-ink">{t("مُضاف لاحقاً", "added later")}</span>;
}
