"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { Settings } from "lucide-react";
import { LoadError, Reveal, SlowNote } from "@/components/ui/Feedback";
import { AiSectionsSkeleton } from "@/components/admin/AdminSkeletons";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { EASE_OUT, stagger } from "@/lib/motion";
import { AiSettingsSection, type AiSection, type Provider } from "@/components/admin/AiSettingsSection";
import { apiFetch, ApiException } from "@/lib/api";
import { useLanguage } from "@/context/LanguageContext";

type AiSettings = { sections: AiSection[]; server_keys: Record<Provider, boolean> };

type UsageModel = {
  provider: string;
  model_id: string;
  turns: number;
  guests: number;
  model_calls: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  cost_per_turn_usd: number | null;
  calls_per_turn: number | null;
};

export default function AdminSettingsPage() {
  const router = useRouter();
  const { dir, t } = useLanguage();
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [usage, setUsage] = useState<UsageModel[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Failed loads in a row (retried by itself; see useAutoRetry).
  const [loadFailures, setLoadFailures] = useState(0);

  const load = useCallback(async () => {
    try {
      setSettings(await apiFetch<AiSettings>("/admin/ai-models"));
      setLoadError(null);
      setLoadFailures(0);
      apiFetch<{ models: UsageModel[] }>("/admin/ai-models/usage?days=7")
        .then((summary) => setUsage(summary.models))
        .catch(() => setUsage(null));
    } catch (error) {
      if (error instanceof ApiException && error.status === 401) {
        router.push("/login");
      } else {
        setLoadError(error instanceof Error ? error.message : t("تعذر تحميل الإعدادات.", "Couldn't load the settings."));
        setLoadFailures((count) => count + 1);
      }
    }
  }, [router, t]);

  // Load once on arrival; each section then updates itself from the server's answer.
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  useAutoRetry(settings ? 0 : loadFailures, () => void load());

  const replaceSection = (updated: AiSection) =>
    setSettings((current) => current && {
      ...current,
      sections: current.sections.map((section) => (section.purpose === updated.purpose ? updated : section)),
    });

  return (
    <div className="flex flex-1 flex-col" dir={dir}>

      <main className="mx-auto w-full max-w-6xl space-y-6 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: EASE_OUT }} className="flex items-start gap-4 border-b border-line pb-5">
          <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow" aria-hidden="true">
            <Settings className="size-6" />
          </span>
          <div>
          <h1 className="font-display text-2xl font-bold text-ink sm:text-3xl">
            {t("إعدادات الذكاء الاصطناعي", "AI settings")}
          </h1>
          <p className="mt-1 text-sm text-muted">
            {t(
              "كل قسم مستقل بمزوّده وموديله ومفتاحه. القسم الذي لم يُحفظ يعمل على إعداد الخادم الافتراضي.",
              "Each section has its own provider, model and key. A section that was never saved runs on the server's default.",
            )}
          </p>
          </div>
        </motion.div>

        <Reveal
          ready={settings !== null || loadFailures > 0}
          label={t("جاري التحميل...", "Loading...")}
          skeleton={<><AiSectionsSkeleton /><SlowNote className="mt-5" /></>}
        >
        {!settings ? (
          <LoadError title={loadError ?? undefined} onRetry={() => void load()} />
        ) : (
          <motion.div initial="hidden" animate="show" variants={stagger(0.05, 0.08)} className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
            {settings.sections.map((section) => (
              <AiSettingsSection
                key={section.purpose}
                section={section}
                serverKeys={settings.server_keys}
                onChange={replaceSection}
              />
            ))}
          </motion.div>
        )}
        </Reveal>

        {/* What the assistant costs (last 7 days) */}
        {usage && usage.length > 0 && (
          <motion.section initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.5, ease: EASE_OUT }} className="rounded-[1.75rem] border border-line bg-surface p-5 text-sm shadow-card" aria-labelledby="usage-title">
            <h2 id="usage-title" className="mb-3 font-display text-lg font-bold text-ink">{t("استهلاك المساعد (آخر 7 أيام)", "Assistant usage (last 7 days)")}</h2>
            {/* Scrolls sideways on phones; LTR values are isolated so every column lines up with its heading. */}
            <div className="no-scrollbar -mx-5 overflow-x-auto px-5">
              <table className="w-full min-w-[40rem] text-start text-xs sm:text-sm">
                <thead className="text-muted">
                  <tr className="[&>th]:whitespace-nowrap [&>th]:py-1.5 [&>th]:pe-4 [&>th]:text-start [&>th]:font-semibold [&>th:last-child]:pe-0">
                    <th>{t("النموذج", "Model")}</th>
                    <th>{t("الرسائل", "Messages")}</th>
                    <th>{t("استدعاءات لكل رسالة", "Calls per message")}</th>
                    <th>{t("رموز (مخزّنة)", "Tokens (cached)")}</th>
                    <th>{t("تكلفة الرسالة", "Cost per message")}</th>
                    <th>{t("المجموع", "Total")}</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.map((row) => (
                    <tr key={`${row.provider}-${row.model_id}`} className="border-t border-line tabular-nums text-ink-2 [&>td]:whitespace-nowrap [&>td]:py-2 [&>td]:pe-4 [&>td:last-child]:pe-0">
                      <td className="font-mono text-ink"><bdi>{row.model_id}</bdi></td>
                      <td>{row.turns}</td>
                      <td>{row.calls_per_turn ?? "—"}</td>
                      <td><bdi>{(row.input_tokens + row.output_tokens).toLocaleString("en")} ({row.input_tokens ? Math.round((row.cached_input_tokens / row.input_tokens) * 100) : 0}%)</bdi></td>
                      <td><bdi>{row.cost_per_turn_usd !== null ? `$${row.cost_per_turn_usd.toFixed(5)}` : "—"}</bdi></td>
                      <td className="font-bold text-ink"><bdi>{row.cost_usd !== null ? `$${row.cost_usd.toFixed(4)}` : t("غير معروف", "unknown")}</bdi></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-1.5 text-[0.6875rem] text-subtle">{t("التكلفة تقديرية حسب أسعار المزوّد المعروفة؛ النسبة بين القوسين هي الرموز المعاد استخدامها من الذاكرة المؤقتة (أرخص بكثير).", "Costs are estimates from known provider prices; the percentage is the share of cached input tokens (much cheaper).")}</p>
          </motion.section>
        )}
      </main>
    </div>
  );
}
