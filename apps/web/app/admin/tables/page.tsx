"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { Ban, Download, ExternalLink, FlaskConical, LoaderCircle, Plus, Printer, QrCode, RefreshCw, RotateCw, TriangleAlert } from "lucide-react";
import { ButtonSpinner, EmptyState, Reveal, SlowNote } from "@/components/ui/Feedback";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { StatusPill } from "@/components/ui/StatusPill";
import { Toast } from "@/components/ui/Toast";
import { EASE_OUT, fadeUp, spring, stagger } from "@/lib/motion";
import { TableCardsSkeleton } from "@/components/admin/AdminSkeletons";
import { QrCodeSvg } from "@/components/admin/QrCodeSvg";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { useLanguage } from "@/context/LanguageContext";
import { apiFetch, ApiException } from "@/lib/api";
import { getPublicSiteUrl, getTableEntryUrl, isDemoMode, isLocalOnlySiteUrl } from "@/lib/config";
import { downloadTableQrPdf } from "@/lib/qrPdf";
import { restaurantTime } from "@/lib/time";

interface TableItem {
  id: string;
  table_number: string;
  seat_count: number;
  is_active: boolean;
  has_active_qr: boolean;
  qr_token_id?: string | null;
  qr_created_at?: string | null;
  qr_token?: string | null;
}

type Notice = { text: string; tone: "success" | "error" };

export default function AdminTablesPage() {
  const router = useRouter();
  const { lang, dir, t } = useLanguage();
  const { showConfirm } = useGlobalDialog();
  const [tables, setTables] = useState<TableItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyTableId, setBusyTableId] = useState<string | null>(null);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // Failed loads in a row (retried by itself; see useAutoRetry).
  const [loadFailures, setLoadFailures] = useState(0);

  const showNotice = (text: string, tone: Notice["tone"] = "success") => {
    setNotice({ text, tone });
    window.setTimeout(() => setNotice(null), 4000);
  };

  const handleAuthError = useCallback((err: unknown): boolean => {
    if (err instanceof ApiException && (err.status === 401 || err.status === 403)) {
      router.push("/login");
      return true;
    }
    return false;
  }, [router]);

  // Loads on open and again whenever "Refresh" bumps reloadKey.
  useEffect(() => {
    let ignore = false;
    apiFetch<TableItem[]>("/admin/tables")
      .then((data) => {
        if (ignore) return;
        setTables(data);
        setLoadError(null);
        setLoadFailures(0);
      })
      .catch((err: unknown) => {
        if (!ignore && !handleAuthError(err)) {
          setLoadError(err instanceof ApiException ? err.message : "");
          setLoadFailures((count) => count + 1);
        }
      })
      .finally(() => {
        if (ignore) return;
        setLoading(false);
        setRefreshing(false);
      });
    return () => {
      ignore = true;
    };
  }, [handleAuthError, reloadKey]);

  const reload = () => {
    setRefreshing(true);
    setReloadKey((key) => key + 1);
  };
  useAutoRetry(tables.length === 0 ? loadFailures : 0, reload);

  const replaceTable = (updated: TableItem) => {
    setTables((current) => current.map((table) => (table.id === updated.id ? updated : table)));
  };

  const handleCreateQr = async (table: TableItem) => {
    if (table.has_active_qr) {
      const confirmed = await showConfirm(
        t(
          `سيتوقف رمز طاولة ${table.table_number} الحالي عن العمل فوراً، وأي نسخة مطبوعة منه لن تفتح الطاولة بعد الآن. هل تريد إنشاء رمز جديد؟`,
          `The current QR for table ${table.table_number} will stop working immediately, including any printed copy. Create a new QR?`,
        ),
        { title: t("استبدال رمز الطاولة", "Replace table QR"), tone: "danger", confirmLabel: t("إنشاء رمز جديد", "Create new QR"), cancelLabel: t("إلغاء", "Cancel") },
      );
      if (!confirmed) return;
    }
    try {
      setBusyTableId(table.id);
      const updated = await apiFetch<TableItem>(`/admin/tables/${table.id}/qr`, { method: "POST" });
      replaceTable(updated);
      showNotice(t(`تم إنشاء رمز جديد لطاولة ${table.table_number}. نزّله واطبعه.`, `A new QR was created for table ${table.table_number}. Download and print it.`));
    } catch (err: unknown) {
      if (handleAuthError(err)) return;
      showNotice(err instanceof ApiException ? err.message : t("تعذر إنشاء الرمز.", "Could not create the QR."), "error");
    } finally {
      setBusyTableId(null);
    }
  };

  const handleDisableQr = async (table: TableItem) => {
    const confirmed = await showConfirm(
      t(
        `سيتوقف رمز طاولة ${table.table_number} عن العمل فوراً ولن يتمكن أحد من فتح الطاولة به حتى تنشئ رمزاً جديداً.`,
        `The QR for table ${table.table_number} will stop working immediately until you create a new one.`,
      ),
      { title: t("تعطيل رمز الطاولة", "Disable table QR"), tone: "danger", confirmLabel: t("تعطيل الرمز", "Disable QR"), cancelLabel: t("إلغاء", "Cancel") },
    );
    if (!confirmed) return;
    try {
      setBusyTableId(table.id);
      const updated = await apiFetch<TableItem>(`/admin/tables/${table.id}/qr`, { method: "DELETE" });
      replaceTable(updated);
      showNotice(t(`تم تعطيل رمز طاولة ${table.table_number}.`, `The QR for table ${table.table_number} was disabled.`));
    } catch (err: unknown) {
      if (handleAuthError(err)) return;
      showNotice(err instanceof ApiException ? err.message : t("تعذر تعطيل الرمز.", "Could not disable the QR."), "error");
    } finally {
      setBusyTableId(null);
    }
  };

  const handleDownload = async (table: TableItem) => {
    if (!table.qr_token) return;
    try {
      setBusyTableId(table.id);
      await downloadTableQrPdf(
        [{ tableNumber: table.table_number, url: getTableEntryUrl(table.qr_token) }],
        `jubran-qr-${table.table_number}.pdf`,
      );
    } catch {
      showNotice(t("تعذر تجهيز ملف PDF.", "Could not prepare the PDF."), "error");
    } finally {
      setBusyTableId(null);
    }
  };

  const printableTables = tables.filter((table) => table.qr_token);

  const handleDownloadAll = async () => {
    if (printableTables.length === 0) return;
    try {
      setDownloadingAll(true);
      await downloadTableQrPdf(
        printableTables.map((table) => ({ tableNumber: table.table_number, url: getTableEntryUrl(table.qr_token as string) })),
        "jubran-qr-all-tables.pdf",
      );
    } catch {
      showNotice(t("تعذر تجهيز ملف PDF.", "Could not prepare the PDF."), "error");
    } finally {
      setDownloadingAll(false);
    }
  };

  const handleTryAsCustomer = (table: TableItem) => {
    if (!table.qr_token) return;
    // Same path a phone takes after scanning the printed QR.
    window.open(`/t/${encodeURIComponent(table.qr_token)}`, "_blank", "noopener");
  };

  const formatDate = (iso?: string | null) =>
    iso ? restaurantTime(iso, lang === "ar" ? "ar-JO" : "en-GB", { dateStyle: "medium", timeStyle: "short" }) : "";

  const activeCount = tables.filter((table) => table.has_active_qr).length;
  const showLocalWarning = !loading && tables.length > 0 && isLocalOnlySiteUrl();

  return (
    <div className="flex flex-1 flex-col" dir={dir}>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: EASE_OUT }}
          className="mb-6 flex flex-col justify-between gap-4 border-b border-line pb-5 lg:flex-row lg:items-end"
        >
          <div className="flex items-start gap-4">
            <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow" aria-hidden="true">
              <QrCode className="size-6" />
            </span>
            <div>
              <h1 className="font-display text-2xl font-bold text-ink sm:text-3xl">{t("إدارة الطاولات ورموز QR", "Tables & QR codes")}</h1>
              <p className="mt-1 max-w-2xl text-sm text-muted">
                {t(
                  "لكل طاولة رمز واحد فعّال. أنشئ الرمز، نزّله PDF واطبعه، وضعه على الطاولة.",
                  "Each table has one active QR. Create it, download the PDF, print it and place it on the table.",
                )}
              </p>
              {!loading && (
                <div className="mt-3 flex items-center gap-3">
                  <div className="h-2 w-40 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
                    <motion.div
                      className="h-full origin-left rounded-full bg-gradient-to-r from-brand to-leaf rtl:origin-right"
                      initial={{ scaleX: 0 }}
                      animate={{ scaleX: tables.length ? activeCount / tables.length : 0 }}
                      transition={{ duration: 0.9, ease: EASE_OUT }}
                    />
                  </div>
                  <p className="text-sm font-semibold text-brand">
                    {t(`${activeCount} من ${tables.length} طاولة لها رمز فعّال`, `${activeCount} of ${tables.length} tables have an active QR`)}
                  </p>
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleDownloadAll()}
              disabled={downloadingAll || printableTables.length === 0}
              className="btn btn-primary"
            >
              {downloadingAll ? <LoaderCircle size={18} aria-hidden="true" className="animate-spin" /> : <Printer size={18} aria-hidden="true" />}
              {downloadingAll ? t("جاري تجهيز الملف...", "Preparing file...") : t("تحميل كل الرموز (PDF)", "Download all QR codes (PDF)")}
            </button>
            <button
              type="button"
              onClick={reload}
              disabled={refreshing}
              className="btn btn-secondary"
            >
              <RefreshCw size={16} aria-hidden="true" className={refreshing ? "animate-spin" : ""} />
              {t("تحديث", "Refresh")}
            </button>
          </div>
        </motion.div>

        <Toast message={notice?.text} tone={notice?.tone === "error" ? "error" : "success"} />

        <div className="space-y-3">
          {loadError !== null && (
            <div role="alert" className="flex flex-wrap items-center gap-2.5 rounded-2xl border border-danger/25 bg-danger-soft p-4 text-sm font-medium text-danger-ink">
              <TriangleAlert size={18} aria-hidden="true" className="shrink-0" />
              <span className="min-w-0 flex-1">{loadError || t("تعذر تحميل بيانات الطاولات.", "Could not load tables.")} {tables.length === 0 && t("بنعيد المحاولة لحالنا.", "We'll keep trying.")}</span>
              <button type="button" onClick={reload} disabled={refreshing} className="btn btn-secondary btn-sm" aria-busy={refreshing}>
                {refreshing ? <ButtonSpinner /> : <RefreshCw size={14} aria-hidden="true" />}
                {t("إعادة المحاولة", "Try again")}
              </button>
            </div>
          )}

          {showLocalWarning && (
            <div className="flex items-start gap-2.5 rounded-2xl border border-warning/30 bg-warning-soft p-4 text-sm leading-relaxed text-warning-ink">
              <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>
                {t(
                  `الرموز الآن تشير إلى ${getPublicSiteUrl()} ولن تفتح من هاتف الزبون. قبل الطباعة افتح لوحة الإدارة من عنوان الشبكة (مثل http://192.168.x.x:3000) أو من عنوان الموقع الحقيقي.`,
                  `QR links currently point to ${getPublicSiteUrl()} and won't open from a customer's phone. Before printing, open the admin panel from the network address (e.g. http://192.168.x.x:3000) or the real site address.`,
                )}
              </span>
            </div>
          )}

          {isDemoMode && (
            <div className="flex items-start gap-2.5 rounded-2xl border border-info/25 bg-info-soft p-4 text-sm leading-relaxed text-info-ink">
              <FlaskConical size={18} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>
                {t(
                  "وضع التجربة مفعّل: زر «جرّب كزبون» يفتح الطاولة كأنك مسحت رمزها الحقيقي. هذا الزر لا يظهر في نسخة الإنتاج.",
                  "Demo mode is on: “Try as customer” opens the table as if you scanned its real QR. This button is hidden in production builds.",
                )}
              </span>
            </div>
          )}
        </div>

        <Reveal
          ready={!loading}
          label={t("جاري تحميل الطاولات...", "Loading tables...")}
          skeleton={<><TableCardsSkeleton /><SlowNote className="mt-5" /></>}
        >
        {tables.length === 0 && loadError === null ? (
          <EmptyState
            className="mt-5"
            icon={<QrCode className="size-7" strokeWidth={1.75} aria-hidden="true" />}
            title={t("لا توجد طاولات بعد", "No tables yet")}
            description={t("الطاولات تُضاف من مخطط الصالة، وبعدها تنشئ لكل طاولة رمزها هنا.", "Tables come from the floor plan; then you create each table's QR here.")}
          />
        ) : (
          <motion.div initial="hidden" animate="show" variants={stagger(0.04, 0.05)} className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {tables.map((table) => {
              const busy = busyTableId === table.id;
              const entryUrl = table.qr_token ? getTableEntryUrl(table.qr_token) : null;
              return (
                <motion.article
                  key={table.id}
                  variants={fadeUp}
                  layout
                  className={`flex flex-col gap-3 rounded-[1.75rem] border bg-surface p-4 shadow-card transition-shadow hover:shadow-lift ${table.has_active_qr ? "border-line" : "border-dashed border-line-strong"}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <span className="rounded-xl bg-brand-soft px-3 py-1.5 font-display text-lg font-bold text-brand-soft-ink" dir="ltr">{table.table_number}</span>
                      <span className="truncate text-sm text-muted">{t(`${table.seat_count} مقاعد`, `${table.seat_count} seats`)}</span>
                    </div>
                    <StatusPill tone={table.has_active_qr ? "success" : "neutral"} dot={table.has_active_qr} size="sm">
                      {table.has_active_qr ? t("رمز فعّال", "QR active") : t("بدون رمز", "No QR")}
                    </StatusPill>
                  </div>

                  {!table.is_active && (
                    <p className="rounded-xl border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning-ink">
                      {t("الطاولة غير مفعّلة، والرمز لن يفتحها حتى يتم تفعيلها.", "This table is inactive; its QR won't open it until it's activated.")}
                    </p>
                  )}

                  <div className="relative flex aspect-square max-h-60 items-center justify-center overflow-hidden rounded-2xl border border-line bg-white p-4 sm:max-h-none">
                    <AnimatePresence mode="wait" initial={false}>
                      {entryUrl ? (
                        <motion.div key={entryUrl} initial={{ opacity: 0, scale: 0.9, rotate: -4 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} exit={{ opacity: 0, scale: 0.9 }} transition={spring.smooth} className="h-full w-full">
                          <QrCodeSvg value={entryUrl} label={t(`رمز QR لطاولة ${table.table_number}`, `QR code for table ${table.table_number}`)} className="mx-auto h-full w-full max-h-[220px] max-w-[220px]" />
                        </motion.div>
                      ) : table.has_active_qr ? (
                        <p key="hidden" className="px-3 text-center text-xs text-amber-800">
                          {t("لا يمكن عرض هذا الرمز لأن مفتاح الخادم تغيّر. أنشئ رمزاً جديداً.", "This QR can't be shown because the server key changed. Create a new one.")}
                        </p>
                      ) : (
                        <motion.div key="none" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center gap-2 text-stone-400">
                          <QrCode size={56} strokeWidth={1.25} aria-hidden="true" />
                          <span className="text-xs">{t("لم يتم إنشاء رمز بعد", "No QR created yet")}</span>
                        </motion.div>
                      )}
                    </AnimatePresence>
                    {busy && (
                      <span className="absolute inset-0 flex items-center justify-center bg-white/70 backdrop-blur-[1px]">
                        <LoaderCircle className="size-7 animate-spin text-brand" aria-hidden="true" />
                      </span>
                    )}
                  </div>

                  {entryUrl && (
                    <div className="space-y-1 text-xs text-muted">
                      <p dir="ltr" className="select-all break-all rounded-xl border border-line bg-surface-2 px-2.5 py-1.5 font-mono">{entryUrl}</p>
                      {table.qr_created_at && <p>{t("أُنشئ في", "Created")} {formatDate(table.qr_created_at)}</p>}
                    </div>
                  )}

                  <div className="mt-auto grid grid-cols-2 gap-2 pt-1">
                    {table.qr_token && (
                      <button type="button" onClick={() => void handleDownload(table)} disabled={busy} className="btn btn-primary btn-sm col-span-2">
                        <Download size={16} aria-hidden="true" />
                        {t("تحميل PDF", "Download PDF")}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleCreateQr(table)}
                      disabled={busy}
                      className={`btn btn-sm ${table.has_active_qr ? "btn-secondary" : "btn-primary col-span-2"}`}
                    >
                      {table.has_active_qr ? <RotateCw size={16} aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
                      {table.has_active_qr ? t("استبدال الرمز", "Replace QR") : t("إنشاء رمز QR", "Create QR")}
                    </button>
                    {table.has_active_qr && (
                      <button type="button" onClick={() => void handleDisableQr(table)} disabled={busy} className="btn btn-danger-soft btn-sm" title={t("تعطيل", "Disable")}>
                        <Ban size={16} aria-hidden="true" />
                        {t("تعطيل", "Disable")}
                      </button>
                    )}
                    {isDemoMode && table.qr_token && (
                      <button type="button" onClick={() => handleTryAsCustomer(table)} className="btn btn-soft btn-sm col-span-2">
                        <ExternalLink size={16} aria-hidden="true" />
                        {t("جرّب كزبون", "Try as customer")}
                      </button>
                    )}
                  </div>
                </motion.article>
              );
            })}
          </motion.div>
        )}
        </Reveal>
      </main>
    </div>
  );
}
