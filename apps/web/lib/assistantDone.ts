/**
 * What an assistant reply really did, as the server recorded it (not what the reply says).
 * Each real action shows as a small badge under the reply, so the guest can trust it.
 */
export interface DoneEntry {
  kind: "basket" | "order_sent" | "order_changed" | "service" | "service_cancelled" | "complaint" | "rating" | string;
  /** service, service_cancelled: STAFF, BILL, TISSUES or CLEAN_TABLE; already_open: it was already on its way. */
  type?: string;
  already_open?: boolean;
  /** complaint: added to the guest's earlier complaint this visit; nothing_new: it said nothing new. */
  added_to_earlier?: boolean;
  nothing_new?: boolean;
  /** rating: the stars given. */
  stars?: number;
  /** order_sent, order_changed (cancelled: the change cancelled the whole order). */
  order_number?: string;
  cancelled?: boolean;
}

const SERVICES: Record<string, [string, string]> = {
  STAFF: ["انطلب موظف", "Staff member called"],
  BILL: ["انطلب الحساب", "Bill requested"],
  TISSUES: ["انطلبت مناديل", "Tissues requested"],
  CLEAN_TABLE: ["انطلب تنظيف الطاولة", "Table cleaning requested"],
};

const CANCELLED_SERVICES: Record<string, [string, string]> = {
  STAFF: ["انلغى طلب الموظف", "Staff request cancelled"],
  BILL: ["انلغى طلب الحساب", "Bill request cancelled"],
  TISSUES: ["انلغى طلب المناديل", "Tissues request cancelled"],
  CLEAN_TABLE: ["انلغى طلب تنظيف الطاولة", "Table cleaning request cancelled"],
};

/**
 * The badges for one reply. Sending or changing an order already shows a card in the reply
 * (`cards`: the order numbers of those cards); their badges are for replies without the card,
 * such as a conversation loaded again. Changes to the basket show in the basket itself.
 */
export function doneBadges(done: DoneEntry[] | undefined, lang: "ar" | "en",
  cards: { sent?: string; changed?: string } = {}): string[] {
  const say = (ar: string, en: string) => `✓ ${lang === "en" ? en : ar}`;
  const badges: string[] = [];
  for (const entry of done ?? []) {
    const number = entry.order_number ?? "";
    if (entry.kind === "service") {
      const [ar, en] = SERVICES[entry.type ?? ""] ?? ["انطلبت الخدمة", "Service requested"];
      badges.push(entry.already_open ? say(`${ar} (من قبل)`, `${en} (earlier)`) : say(ar, en));
    } else if (entry.kind === "service_cancelled") {
      const [ar, en] = CANCELLED_SERVICES[entry.type ?? ""] ?? ["انلغى الطلب", "Request cancelled"];
      badges.push(say(ar, en));
    } else if (entry.kind === "complaint") {
      badges.push(entry.nothing_new ? say("شكواك مسجّلة", "Your complaint is on record")
        : entry.added_to_earlier ? say("انضافت لشكواك", "Added to your complaint")
          : say("انسجلت الشكوى", "Complaint recorded"));
    } else if (entry.kind === "rating" && entry.stars) {
      badges.push(say(`انحفظ التقييم ${entry.stars} من 5`, `Rating saved: ${entry.stars} of 5`));
    } else if (entry.kind === "order_sent" && number !== cards.sent) {
      badges.push(say(`انبعت الطلب ${number}`, `Order ${number} sent`));
    } else if (entry.kind === "order_changed" && number !== cards.changed) {
      badges.push(entry.cancelled ? say(`انلغى الطلب ${number}`, `Order ${number} cancelled`)
        : say(`تعدّل الطلب ${number}`, `Order ${number} changed`));
    }
  }
  return [...new Set(badges)];
}
