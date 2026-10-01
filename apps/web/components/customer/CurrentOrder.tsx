"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight, BookOpenText, CircleAlert, ClipboardCheck, LoaderCircle, Plus, Send, ShoppingBag, StickyNote, Trash2,
} from "lucide-react";
import { apiFetch, ApiException } from "@/lib/api";
import { useDraftChanged } from "@/lib/draftEvents";
import { useLanguage } from "@/context/LanguageContext";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { Sheet, SheetBody, SheetFooter, SheetHeader } from "@/components/ui/Sheet";
import { QuantityStepper } from "@/components/ui/QuantityStepper";
import { ButtonSpinner, EmptyState, LoadError, Reveal } from "@/components/ui/Feedback";
import { BasketRowsSkeleton } from "@/components/customer/GuestSkeletons";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { StatusPill } from "@/components/ui/StatusPill";
import { spring } from "@/lib/motion";
import { otherLanguage } from "@/lib/otherLanguage";

/** Same limit as the server (MAX_LINE_QUANTITY). */
const MAX_LINE_QUANTITY = 50;

interface DraftItem {
  line_id: string;
  product_id: string;
  name_ar: string;
  name_en: string;
  unit_price_minor: number;
  unit_price_display_ar: string;
  unit_price_display_en?: string;
  quantity: number;
  note?: string;
  line_total_minor: number;
  line_total_display_ar: string;
  line_total_display_en?: string;
  is_available: boolean;
}

interface DraftSummary {
  draft_id: string;
  version: number;
  status: string;
  items: DraftItem[];
  total_minor: number;
  total_display_ar: string;
  total_display_en?: string;
  item_count: number;
}

interface ConfirmationResponse {
  draft_version: number;
  confirmation_token: string;
  summary: DraftSummary;
}

interface SubmitResponse {
  order_id: string;
  order_number: string;
  status: string;
}

/**
 * The guest's basket: dishes picked but not sent to the kitchen yet (sent orders are
 * listed separately on the orders page, so the two are never confused).
 */
export function CurrentOrder({ onSubmitted, onDraftCountChange, variant = "page", hasSentOrders = false }: {
  onSubmitted: () => void;
  onDraftCountChange: (count: number) => void;
  /** "panel": the compact basket next to the menu on large screens. */
  variant?: "page" | "panel";
  /** The guest already sent orders: an empty basket is then just a slim "order more" line. */
  hasSentOrders?: boolean;
}) {
  const { lang, dir, t } = useLanguage();
  // Prices in the page language (older answers without English fall back to Arabic).
  const price = (ar: string, en?: string) => (lang === "en" && en ? en : ar);
  const { showAlert } = useGlobalDialog();
  const [draft, setDraft] = useState<DraftSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [confirmModal, setConfirmModal] = useState<ConfirmationResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The basket line being changed right now, and "continue to confirm" on its way.
  const [busyLine, setBusyLine] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  // Failed first loads in a row (retried by itself; see useAutoRetry).
  const [loadFailures, setLoadFailures] = useState(0);

  const fetchDraft = async (showLoading = true) => {
    try {
      if (showLoading) {
        setLoading(true);
        setError(null);
      }
      const data = await apiFetch<DraftSummary>("/draft");
      setDraft(data);
      setError(null);
      setLoadFailures(0);
      onDraftCountChange(data.item_count);
    } catch (err: unknown) {
      if (showLoading) {
        setLoadFailures((count) => count + 1);
        if (err instanceof ApiException) {
          setError(err.message);
        } else {
          setError(t("تعذر تحميل سلتك.", "Could not load your basket."));
        }
      }
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  useEffect(() => {
    void Promise.resolve().then(() => fetchDraft());
    // Loaded once; later changes arrive through useDraftChanged below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The assistant changed the basket in the chat panel: show it here right away.
  useDraftChanged(() => void fetchDraft(false));
  useAutoRetry(draft ? 0 : loadFailures, () => void fetchDraft());

  const handleUpdateQuantity = async (lineId: string, newQty: number) => {
    if (busyLine) return; // one change at a time, so quick taps never cross each other
    try {
      setBusyLine(lineId);
      const updated = await apiFetch<DraftSummary>(`/draft/items/${lineId}`, {
        method: "PATCH",
        body: JSON.stringify({ quantity: newQty }),
      });
      setDraft(updated);
      onDraftCountChange(updated.item_count);
    } catch (err: unknown) {
      if (err instanceof ApiException) void showAlert(err.message);
    } finally {
      setBusyLine(null);
    }
  };

  const handleRemove = async (lineId: string) => {
    if (busyLine) return;
    try {
      setBusyLine(lineId);
      const updated = await apiFetch<DraftSummary>(`/draft/items/${lineId}`, {
        method: "DELETE",
      });
      setDraft(updated);
      onDraftCountChange(updated.item_count);
    } catch (err: unknown) {
      if (err instanceof ApiException) void showAlert(err.message);
    } finally {
      setBusyLine(null);
    }
  };

  const handlePrepareConfirmation = async () => {
    if (preparing) return;
    try {
      setPreparing(true);
      setError(null);
      const prep = await apiFetch<ConfirmationResponse>("/draft/prepare-confirmation", {
        method: "POST",
      });
      setConfirmModal(prep);
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        setError(err.message);
      } else {
        setError(t("تعذر إعداد تأكيد الطلب.", "Could not prepare order confirmation."));
      }
    } finally {
      setPreparing(false);
    }
  };

  const handleConfirmAndSubmit = async () => {
    if (!confirmModal) return;

    try {
      setSubmitting(true);
      setError(null);

      // Generate client-side idempotency key for this order confirmation
      const idempotencyKey = `ord-${confirmModal.summary.draft_id}-v${confirmModal.draft_version}`;

      await apiFetch<SubmitResponse>("/orders", {
        method: "POST",
        headers: {
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          confirmation_token: confirmModal.confirmation_token,
          draft_version: confirmModal.draft_version,
        }),
      });

      setConfirmModal(null);
      setDraft(null);
      onDraftCountChange(0);
      onSubmitted();
      await fetchDraft(false);
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        if (err.code === "DRAFT_VERSION_CONFLICT") {
          // Version changed while confirming: reload draft
          setConfirmModal(null);
          await fetchDraft(false);
          setError(err.details?.reason === "SUMMARY_CHANGED"
            ? t("تغيّرت أسعار أو أصناف طلبك بعد ما راجعته. راجع الطلب المحدّث وأكّده من جديد.", "Prices or items changed after you reviewed your order. Please review the updated order and confirm again.")
            : t("تغيّرت سلتك قبل قليل. راجع الأصناف المحدّثة وأكّدها من جديد.", "Your basket changed a moment ago. Please review the updated items."));
        } else {
          setError(err.message);
        }
      } else {
        setError(t("فشلت عملية إرسال الطلب. يرجى المحاولة ثانية.", "Failed to place order. Please try again."));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const closeConfirm = () => {
    if (!submitting) setConfirmModal(null);
  };
  const panel = variant === "panel";
  const hasItems = Boolean(draft && draft.items.length > 0);
  // The basket itself could not be loaded (not an error about sending it).
  const loadFailed = !loading && !draft && loadFailures > 0;
  // After an order went out, an empty basket is only an invitation to order more.
  const slimEmpty = !panel && hasSentOrders && !loading && !hasItems && !error;

  if (slimEmpty) {
    return (
      <motion.section
        layout
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={spring.smooth}
        dir={dir}
        aria-labelledby="current-order-title"
        className="flex flex-col gap-3 rounded-3xl border border-dashed border-brand-line bg-brand-soft/40 p-4 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex items-center gap-3">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-surface text-brand shadow-card">
            <ShoppingBag className="size-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 id="current-order-title" className="font-bold text-ink">{t("بدك تطلب كمان؟", "Want anything else?")}</h2>
            <p className="text-sm text-muted">{t("سلتك فارغة، وأي أصناف تضيفها تصير طلباً جديداً.", "Your basket is empty; anything you add becomes a new order.")}</p>
          </div>
        </div>
        <Link href="/menu" className="btn btn-primary btn-sm shrink-0 self-start sm:self-auto">
          <Plus className="size-4" aria-hidden="true" />
          {t("أضف أصنافاً", "Add dishes")}
        </Link>
      </motion.section>
    );
  }

  return (
    <section className="@container" dir={dir} aria-labelledby="current-order-title">
      <div className="mb-4 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id="current-order-title" className={`font-display font-bold leading-tight text-ink ${panel ? "text-lg" : "text-xl sm:text-2xl"}`}>
            {t("سلتك", "Your basket")}
          </h2>
          {hasItems && draft ? (
            <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted">
              {/* The one thing that sets it apart from a sent order: it hasn't gone to the kitchen. */}
              <StatusPill tone="warning" size="sm" dot>{t("لم تُرسل للمطبخ بعد", "Not sent to the kitchen yet")}</StatusPill>
              <span className="tabular-nums">{t(`${draft.item_count} صنف`, `${draft.item_count} ${draft.item_count === 1 ? "item" : "items"}`)}</span>
            </p>
          ) : !panel && (
            <p className="mt-0.5 text-sm text-muted">{t("الأصناف التي تختارها قبل إرسالها للمطبخ", "The dishes you pick before sending them to the kitchen")}</p>
          )}
        </div>
        {!panel && draft && draft.items.length > 0 && (
          <Link href="/menu" className="btn btn-soft btn-sm shrink-0">
            <Plus className="size-4" aria-hidden="true" />
            {t("أضف أصنافاً", "Add dishes")}
          </Link>
        )}
      </div>

      <AnimatePresence>
        {error && !loadFailed && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            role="alert"
            className="mb-4 flex items-start gap-2.5 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm font-medium text-danger-ink"
          >
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <Reveal ready={!loading || loadFailures > 0} skeleton={<BasketRowsSkeleton />} label={t("جاري تحميل سلتك...", "Loading your basket...")}>
      {loadFailed || (loading && !draft) ? (
        <LoadError title={t("ما قدرنا نحمّل سلتك", "We couldn't load your basket")} onRetry={() => void fetchDraft()} retrying={loading} />
      ) : !draft || draft.items.length === 0 ? (
        panel ? (
          <div className="rounded-3xl border border-dashed border-line-strong bg-surface-2/60 px-5 py-8 text-center">
            <ShoppingBag className="mx-auto mb-3 size-8 text-brand/60" strokeWidth={1.5} aria-hidden="true" />
            <p className="font-semibold text-ink">{t("سلتك فارغة", "Your basket is empty")}</p>
            <p className="mt-1 text-sm text-muted">{t("اختر ما تشتهيه من القائمة، وسيظهر هنا.", "Pick what you fancy from the menu and it will appear here.")}</p>
          </div>
        ) : (
          <EmptyState
            icon={<ShoppingBag className="size-7" strokeWidth={1.75} aria-hidden="true" />}
            title={t("سلتك فارغة", "Your basket is empty")}
            description={t(
              "اختر أطباقك من القائمة، وبعد تأكيد الطلب يُرسل إلى المطبخ وتتابع حالته من هنا.",
              "Pick your dishes from the menu. Once you confirm, the order goes to the kitchen and you can follow it here."
            )}
            action={
              <Link href="/menu" className="btn btn-primary">
                <BookOpenText className="size-[18px]" aria-hidden="true" />
                {t("تصفح قائمة الطعام", "Browse the menu")}
              </Link>
            }
          />
        )
      ) : (
        <div className="space-y-3">
          <ul className="overflow-hidden rounded-3xl border border-line bg-surface shadow-card">
            <AnimatePresence initial={false}>
              {draft.items.map((item) => (
                <motion.li
                  key={item.line_id}
                  layout
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={spring.smooth}
                  className="border-b border-line last:border-b-0"
                >
                  <div className="flex flex-col gap-3 p-4 @md:flex-row @md:items-center">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="font-bold leading-snug text-ink">{lang === "ar" ? item.name_ar : item.name_en}</h3>
                          <p className="text-end text-xs text-subtle" {...otherLanguage(lang)}>{lang === "ar" ? item.name_en : item.name_ar}</p>
                        </div>
                        <span className="shrink-0 font-bold tabular-nums text-ink @md:hidden">{price(item.line_total_display_ar, item.line_total_display_en)}</span>
                      </div>
                      <p className="mt-1 text-sm text-muted">
                        <span className="tabular-nums">{price(item.unit_price_display_ar, item.unit_price_display_en)}</span> {t("للواحدة", "each")}
                      </p>
                      {item.note && (
                        <p className="mt-2 inline-flex max-w-full items-start gap-1.5 rounded-xl bg-accent-soft px-2.5 py-1.5 text-xs text-accent-ink">
                          <StickyNote className="mt-px size-3.5 shrink-0" aria-hidden="true" />
                          <span className="min-w-0 break-words">{item.note}</span>
                        </p>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-3 @md:justify-end">
                      <span className="hidden min-w-20 text-end font-bold tabular-nums text-ink @md:block">{price(item.line_total_display_ar, item.line_total_display_en)}</span>
                      <div className="flex items-center gap-1.5">
                        <QuantityStepper
                          value={item.quantity}
                          onDecrement={() => handleUpdateQuantity(item.line_id, item.quantity - 1)}
                          onIncrement={() => handleUpdateQuantity(item.line_id, item.quantity + 1)}
                          decrementDisabled={busyLine !== null}
                          incrementDisabled={busyLine !== null || item.quantity >= MAX_LINE_QUANTITY}
                          size="sm"
                          label={t(`كمية ${lang === "ar" ? item.name_ar : item.name_en}`, `Quantity of ${item.name_en}`)}
                        />
                        <button
                          type="button"
                          onClick={() => handleRemove(item.line_id)}
                          disabled={busyLine !== null}
                          aria-busy={busyLine === item.line_id}
                          aria-label={t(`حذف ${lang === "ar" ? item.name_ar : item.name_en}`, `Remove ${item.name_en}`)}
                          title={t("حذف", "Remove")}
                          className="flex size-10 items-center justify-center rounded-full text-subtle transition-colors hover:bg-danger-soft hover:text-danger disabled:opacity-60"
                        >
                          {busyLine === item.line_id ? <ButtonSpinner className="size-[18px] text-brand" /> : <Trash2 className="size-[18px]" aria-hidden="true" />}
                        </button>
                      </div>
                    </div>
                  </div>
                  {item.quantity >= MAX_LINE_QUANTITY && (
                    <p className="-mt-2 px-4 pb-3 text-xs text-subtle">{t(`الحد الأقصى ${MAX_LINE_QUANTITY} للصنف الواحد`, `Maximum ${MAX_LINE_QUANTITY} per item`)}</p>
                  )}
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>

          {/* The authoritative total */}
          <div className="rounded-3xl border border-line bg-surface-2 p-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-semibold text-ink-2">{t("المجموع", "Total")}</span>
              <motion.span
                key={draft.total_minor}
                initial={{ opacity: 0.4, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={spring.smooth}
                className="font-display text-2xl font-bold leading-none tabular-nums text-brand"
              >
                {price(draft.total_display_ar, draft.total_display_en)}
              </motion.span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-subtle">
              {t("المجموع محسوب من أسعار المطعم الحالية، ويُراجع مرة أخرى عند التأكيد.",
                "The total uses the restaurant's current prices and is checked again when you confirm.")}
            </p>
          </div>

          <button onClick={handlePrepareConfirmation} disabled={preparing} aria-busy={preparing} className="btn btn-primary btn-lg w-full">
            {preparing && <ButtonSpinner className="size-5" />}
            <span>{preparing ? t("جاري تجهيز الملخص...", "Preparing the summary...") : t("متابعة لتأكيد الطلب", "Continue to confirm")}</span>
            {!preparing && <ArrowRight className="size-5 rtl:-scale-x-100" aria-hidden="true" />}
          </button>
        </div>
      )}
      </Reveal>

      {/* Confirmation (server-enforced): exactly what the kitchen will receive */}
      <Sheet open={confirmModal !== null} onClose={closeConfirm} labelledBy="confirm-order-title" size="md">
        {confirmModal && (
          <>
            <SheetHeader
              id="confirm-order-title"
              icon={<ClipboardCheck className="size-6" aria-hidden="true" />}
              title={t("تأكيد إرسال الطلب", "Confirm & send order")}
              subtitle={t("راجع الأصناف قبل إرسالها إلى المطبخ", "Review your items before sending them to the kitchen")}
              onClose={submitting ? undefined : closeConfirm}
            />
            <SheetBody>
              <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
                {confirmModal.summary.items.map((item) => (
                  <li key={item.line_id} className="flex items-start justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-ink">
                        <span className="me-1.5 inline-flex min-w-7 justify-center rounded-lg bg-brand-soft px-1.5 py-0.5 text-sm font-bold tabular-nums text-brand-soft-ink">{item.quantity}×</span>
                        {lang === "ar" ? item.name_ar : item.name_en}
                      </p>
                      {item.note && <p className="mt-1 text-xs text-accent-ink">{item.note}</p>}
                    </div>
                    <span className="shrink-0 font-bold tabular-nums text-ink">{price(item.line_total_display_ar, item.line_total_display_en)}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex items-baseline justify-between rounded-2xl bg-brand-soft px-4 py-3.5">
                <span className="font-semibold text-brand-soft-ink">{t("المجموع النهائي", "Final total")}</span>
                <span className="font-display text-2xl font-bold leading-none tabular-nums text-brand">
                  {price(confirmModal.summary.total_display_ar, confirmModal.summary.total_display_en)}
                </span>
              </div>
            </SheetBody>
            <SheetFooter>
              <button type="button" onClick={() => setConfirmModal(null)} disabled={submitting} className="btn btn-secondary">
                {t("مراجعة الطلب", "Review order")}
              </button>
              <button type="button" onClick={handleConfirmAndSubmit} disabled={submitting} className="btn btn-primary flex-1">
                {submitting ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Send className="size-[18px] rtl:-scale-x-100" aria-hidden="true" />}
                {submitting ? t("جاري الإرسال للمطبخ...", "Sending to the kitchen...") : t("تأكيد وإرسال الطلب", "Confirm & place order")}
              </button>
            </SheetFooter>
          </>
        )}
      </Sheet>
    </section>
  );
}
