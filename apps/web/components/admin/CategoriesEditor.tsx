"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, FolderTree, LoaderCircle, Plus, Save, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Skeleton } from "@/components/ui/Feedback";
import { EASE_EMPHASIZED, spring } from "@/lib/motion";
import { useLanguage } from "@/context/LanguageContext";

type Category = {
  id?: string;
  name_ar: string;
  name_en: string;
  sort_order: number;
};

/** A row on screen: `key` stays the same while the row exists (new rows have no id yet). */
type Row = Category & { key: string };

export function CategoriesEditor({ onSaved }: { onSaved: () => Promise<void> }) {
  const { t } = useLanguage();
  const [categories, setCategories] = useState<Row[]>([]);
  const nextKey = useRef(0);
  const withKeys = (list: Category[]): Row[] => list.map((category) => ({ ...category, key: category.id ?? `new-${nextKey.current++}` }));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [open, setOpen] = useState(false);

  // Loaded once: switching the interface language must not throw away unsaved edits.
  useEffect(() => {
    let active = true;
    apiFetch<{ categories: Category[] }>("/admin/menu/categories")
      .then((result) => { if (active) setCategories(withKeys(result.categories)); })
      .catch((error: unknown) => { if (active) setNotice({ text: error instanceof Error ? error.message : t("تعذر تحميل الأقسام.", "Could not load categories."), error: true }); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once
  }, []);

  const edit = (key: string, patch: Partial<Category>) => {
    setCategories((current) => current.map((category) => category.key === key ? { ...category, ...patch } : category));
  };

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const result = await apiFetch<{ categories: Category[] }>("/admin/menu/categories", {
        method: "PUT",
        body: JSON.stringify({ categories: categories.map(({ id, name_ar, name_en, sort_order }) => ({ id, name_ar: name_ar.trim(), name_en: name_en.trim(), sort_order })) }),
      });
      setCategories(withKeys(result.categories));
      await onSaved();
      setNotice({ text: t("حُفظت الأقسام.", "Categories saved."), error: false });
    } catch (error: unknown) {
      setNotice({ text: error instanceof Error ? error.message : t("تعذر الحفظ.", "Could not save."), error: true });
    } finally {
      setSaving(false);
    }
  };

  return <section className="mb-5 overflow-hidden rounded-[1.5rem] border border-line bg-surface shadow-card">
    <button
      type="button"
      onClick={() => setOpen((current) => !current)}
      aria-expanded={open}
      className="flex w-full items-center justify-between gap-3 p-4 text-start sm:p-5"
    >
      <span className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent-ink"><FolderTree className="size-5" aria-hidden="true" /></span>
        <span>
          <span className="block font-display text-lg font-bold text-ink">{t("أقسام قائمة الطعام", "Menu categories")}</span>
          <span className="block text-sm text-muted">{loading ? t("جاري تحميل الأقسام...", "Loading categories...") : t(`${categories.length} أقسام · اضغط للترتيب أو التعديل`, `${categories.length} categories · tap to reorder or rename`)}</span>
        </span>
      </span>
      <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring.snappy} className="flex size-9 items-center justify-center rounded-full bg-surface-3 text-ink-2">
        <ChevronDown className="size-5" aria-hidden="true" />
      </motion.span>
    </button>

    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          key="categories"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.35, ease: EASE_EMPHASIZED }}
          className="overflow-hidden"
        >
          <div className="space-y-3 border-t border-line p-4 sm:p-5">
            <p className="text-sm text-muted">{t("رتّب الأقسام أو غيّر أسماءها. كل قسم موجود يظهر تلقائياً. لن يُحذف قسم يحتوي على أطباق حتى تنقلها أو تحذفها.", "Rename or reorder categories. Every existing category appears automatically. Move or delete its dishes before deleting it.")}</p>
            <AnimatePresence>
              {notice && (
                <motion.div
                  role="status"
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className={`rounded-2xl border px-4 py-3 text-sm font-medium ${notice.error ? "border-danger/25 bg-danger-soft text-danger-ink" : "border-success/25 bg-success-soft text-success-ink"}`}
                >
                  {notice.text}
                </motion.div>
              )}
            </AnimatePresence>
            {loading ? (
              <div className="space-y-2"><Skeleton className="h-16 w-full rounded-2xl" /><Skeleton className="h-16 w-full rounded-2xl" /></div>
            ) : <>
              {categories.length === 0 && (
                <div className="rounded-2xl border border-dashed border-line-strong bg-surface-2 px-4 py-6 text-center text-sm text-muted">
                  {t("لا توجد أقسام بعد. أضف أول قسم للبدء.", "No categories yet. Add your first category to get started.")}
                </div>
              )}
              <AnimatePresence initial={false}>
                {categories.map((category) => (
                  <motion.div
                    key={category.key}
                    layout
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={spring.smooth}
                    className="overflow-hidden"
                  >
                    <div className="grid grid-cols-1 gap-3 rounded-2xl border border-line bg-surface-2 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_6rem_auto] sm:items-end sm:p-4">
                      <div className="min-w-0">
                        <label htmlFor={`category-${category.key}-ar`} className="field-label text-xs">{t("الاسم بالعربية", "Arabic name")}</label>
                        <input id={`category-${category.key}-ar`} dir="rtl" value={category.name_ar} onChange={(event) => edit(category.key, { name_ar: event.target.value })} placeholder={t("مثال: القائمة الرئيسية", "e.g. Main Menu")} className="input" />
                      </div>
                      <div className="min-w-0">
                        <label htmlFor={`category-${category.key}-en`} className="field-label text-xs">{t("الاسم بالإنجليزية", "English name")}</label>
                        <input id={`category-${category.key}-en`} value={category.name_en} onChange={(event) => edit(category.key, { name_en: event.target.value })} placeholder={t("مثال: Main Menu", "e.g. Main Menu")} dir="ltr" className="input" />
                      </div>
                      <div>
                        <label htmlFor={`category-${category.key}-order`} className="field-label text-xs">{t("الترتيب", "Order")}</label>
                        <input id={`category-${category.key}-order`} type="number" min={0} value={category.sort_order} onChange={(event) => edit(category.key, { sort_order: Number(event.target.value) })} title={t("الترتيب", "Sort order")} className="input text-center font-semibold tabular-nums text-brand" />
                      </div>
                      <button type="button" onClick={() => setCategories((current) => current.filter((row) => row.key !== category.key))} aria-label={t("حذف القسم", "Delete category")} className="btn btn-danger-soft min-h-[2.875rem]">
                        <Trash2 className="size-4" aria-hidden="true" />
                        <span>{t("حذف", "Delete")}</span>
                      </button>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
              <div className="flex flex-wrap gap-2 pt-1">
                <button type="button" onClick={() => setCategories((current) => [...current, { key: `new-${nextKey.current++}`, name_ar: "", name_en: "", sort_order: Math.max(0, ...current.map((category) => category.sort_order)) + 10 }])}
                  className="btn btn-soft">
                  <Plus className="size-4" aria-hidden="true" />
                  {t("إضافة قسم", "Add category")}
                </button>
                <button type="button" disabled={saving} onClick={save} className="btn btn-primary">
                  {saving ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" aria-hidden="true" />}
                  {saving ? t("جاري الحفظ...", "Saving...") : t("حفظ الأقسام", "Save categories")}
                </button>
              </div>
            </>}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  </section>;
}
