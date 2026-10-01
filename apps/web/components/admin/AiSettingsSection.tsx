"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AudioLines, Bot, KeyRound, Mic, Search } from "lucide-react";
import { fadeUp, spring } from "@/lib/motion";
import { SelectField } from "@/components/common/SelectField";
import { ButtonSpinner } from "@/components/ui/Feedback";
import { apiFetch, ApiException } from "@/lib/api";
import { useLanguage } from "@/context/LanguageContext";

export type Provider = "openai" | "gemini";
export type Purpose = "chat" | "embedding" | "transcription" | "voice";

type SavedSetting = {
  model_id: string;
  has_api_key: boolean;
  is_active: boolean;
  reasoning_effort: string | null;
  thinking_level: string | null;
};

/** One purpose's settings as the API describes them (keys are never included). */
export type AiSection = {
  purpose: Purpose;
  providers: Provider[];
  suggested_models: Partial<Record<Provider, string>>;
  in_use: {
    source: "saved" | "default" | "none";
    provider: Provider | null;
    model_id: string | null;
    key: "saved" | "server" | null;
  };
  saved: Partial<Record<Provider, SavedSetting>>;
  indexed_documents?: number;
};

type Form = { provider: Provider; model_id: string; api_key: string; thinking: string };
type Feedback = { text: string; error: boolean };

const REASONING_LEVELS: Record<Provider, string[]> = {
  openai: ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
  gemini: ["minimal", "low", "medium", "high"],
};

function formFor(section: AiSection, provider?: Provider): Form {
  const inUse = section.in_use.provider;
  const chosen = provider ?? (inUse && section.providers.includes(inUse) ? inUse : section.providers[0]);
  const saved = section.saved[chosen];
  return {
    provider: chosen,
    model_id: saved?.model_id ?? section.suggested_models[chosen] ?? "",
    api_key: "",
    thinking: (chosen === "openai" ? saved?.reasoning_effort : saved?.thinking_level) ?? "",
  };
}

export function AiSettingsSection({ section, serverKeys, onChange }: {
  section: AiSection;
  serverKeys: Record<Provider, boolean>;
  onChange: (section: AiSection) => void;
}) {
  const { lang, t } = useLanguage();
  const [form, setForm] = useState<Form>(() => formFor(section));
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | "off" | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const { purpose, in_use: inUse } = section;
  const isVoice = purpose === "voice";
  const voiceOn = isVoice && inUse.source === "saved";
  const savedHere = section.saved[form.provider];
  const providerName = (provider: Provider | null) =>
    provider === "openai" ? (isVoice ? "OpenAI GPT-Live" : "OpenAI") : "Google Gemini";

  const copy = {
    chat: {
      icon: Bot,
      title: t("المحادثة (الشات بوت)", "Chat (the assistant)"),
      about: t(
        "الموديل الذي يتحدث مع الزبون ويأخذ الطلبات ويستخدم الأدوات.",
        "The model that talks with guests, takes orders and uses the tools.",
      ),
    },
    embedding: {
      icon: Search,
      title: t("البحث في المنيو (Embeddings)", "Menu search (embeddings)"),
      about: t(
        "يفهم معنى كلام الزبون ليجد الأصناف. تغيير المزوّد أو الموديل يعيد فهرسة المنيو تلقائياً.",
        "Understands what guests mean to find dishes. Changing the provider or model re-indexes the menu automatically.",
      ),
    },
    transcription: {
      icon: Mic,
      title: t("تحويل الصوت إلى نص (الإملاء)", "Speech to text (dictation)"),
      about: t(
        "يحوّل تسجيل الزبون إلى نص: زر المايك في المحادثة، والصوت رسالةً برسالة.",
        "Turns a guest's recording into text: the chat's microphone button and message-by-message voice.",
      ),
    },
    voice: {
      icon: AudioLines,
      title: t("الصوت المباشر (Speech)", "Live voice (speech)"),
      about: t(
        "مكالمة صوتية مباشرة عبر OpenAI GPT-Live: يسمع الزبون ويحكي معه، وكل طلب يمر على موديل المحادثة وأدواته وحراسه نفسها. وهو مطفأ، يعمل زر الصوت رسالةً برسالة: الإملاء يحوّل الكلام إلى نص، والمحادثة ترد، والمتصفح يقرأ الرد.",
        "A live voice call through OpenAI GPT-Live: it listens and speaks, and every request goes through the chat model with the same tools and guards. While it's off, the voice button works message by message: speech to text, the chat replies, and the browser reads it aloud.",
      ),
    },
  }[purpose];

  const badge =
    inUse.source === "saved"
      ? { text: t("مُفعّل", "In use"), style: "bg-brand-soft text-brand border-brand-line" }
      : inUse.source === "default"
        ? { text: t("افتراضي من الخادم", "Server default"), style: "bg-info-soft text-info-ink border-info/25" }
        : isVoice
          ? { text: t("مطفأ", "Off"), style: "bg-surface-3 text-muted border-line" }
          : { text: t("غير مضبوط", "Not set up"), style: "bg-warning-soft text-warning-ink border-warning/25" };

  const keyLabel =
    inUse.key === "saved" ? t("مفتاح محفوظ", "saved key")
      : inUse.key === "server" ? t("مفتاح الخادم", "server key")
        : t("بلا مفتاح", "no key");

  const keyPlaceholder = savedHere?.has_api_key
    ? t("المفتاح محفوظ — اتركه فارغاً للإبقاء عليه", "Key saved — leave empty to keep it")
    : serverKeys[form.provider]
      ? t("اختياري — إذا تُرك فارغاً يُستخدم مفتاح الخادم", "Optional — empty uses the server key")
      : t("الصق مفتاح API هنا", "Paste the API key here");
  const keyNeeded = !savedHere?.has_api_key && !serverKeys[form.provider];

  const errorText = (error: unknown, fallback: string) => {
    if (error instanceof ApiException) {
      const english = error.details?.message_en;
      return lang === "en" && typeof english === "string" ? english : error.message;
    }
    return error instanceof Error ? error.message : fallback;
  };

  const payload = () => ({
    provider: form.provider,
    model_id: form.model_id.trim(),
    api_key: form.api_key.trim() || null,
    reasoning_effort: purpose === "chat" && form.provider === "openai" ? form.thinking || null : null,
    thinking_level: purpose === "chat" && form.provider === "gemini" ? form.thinking || null : null,
  });

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setFeedback(null);
    try {
      const updated = await apiFetch<AiSection>(`/admin/ai-models/${purpose}`, {
        method: "PUT",
        body: JSON.stringify(payload()),
      });
      onChange(updated);
      setForm(formFor(updated));
      const using = `${providerName(updated.in_use.provider)} · ${updated.in_use.model_id}`;
      let text = t(`تم الحفظ. القسم يستخدم الآن ${using}.`, `Saved. This section now uses ${using}.`);
      if (purpose === "embedding") {
        text += updated.indexed_documents
          ? t(` وتمت فهرسة المنيو (${updated.indexed_documents} عنصر).`, ` The menu is indexed (${updated.indexed_documents} items).`)
          : t(" ستتم فهرسة المنيو عند أول بحث.", " The menu will be indexed on the first search.");
      }
      setFeedback({ text, error: false });
    } catch (error) {
      setFeedback({ text: errorText(error, t("تعذر الحفظ.", "Couldn't save.")), error: true });
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    setBusy("test");
    setFeedback(null);
    try {
      await apiFetch(`/admin/ai-models/${purpose}/test`, { method: "POST", body: JSON.stringify(payload()) });
      setFeedback({ text: t("✓ الاتصال ناجح.", "✓ The connection works."), error: false });
    } catch (error) {
      setFeedback({ text: errorText(error, t("فشل فحص الاتصال.", "The connection check failed.")), error: true });
    } finally {
      setBusy(null);
    }
  };

  const switchOff = async () => {
    setBusy("off");
    setFeedback(null);
    try {
      onChange(await apiFetch<AiSection>(`/admin/ai-models/${purpose}`, { method: "DELETE" }));
      setFeedback({ text: t("تم إيقاف الصوت المباشر.", "Live voice is off."), error: false });
    } catch (error) {
      setFeedback({ text: errorText(error, t("تعذر الإيقاف.", "Couldn't switch it off.")), error: true });
    } finally {
      setBusy(null);
    }
  };

  const inputClass =
    "w-full font-mono text-xs sm:text-sm rounded-xl border border-line-strong bg-surface-2 px-3.5 py-2.5 text-left text-ink placeholder:font-sans placeholder:text-subtle focus:bg-surface focus:outline-none focus:ring-2 focus:ring-brand focus:border-transparent transition-all";
  const labelClass = "block text-xs font-bold text-ink-2 mb-1.5";

  return (
    <motion.section
      variants={fadeUp}
      aria-labelledby={`${purpose}-title`}
      className={`flex flex-col gap-4 rounded-[1.75rem] border bg-surface p-5 shadow-card sm:p-6 ${
        inUse.source === "saved" ? "border-brand/40" : "border-line"
      }`}
    >
      <header className="space-y-1.5">
        <div className="flex items-start justify-between gap-3">
          <h2 id={`${purpose}-title`} className="flex items-center gap-3 font-display text-lg font-bold text-ink">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-brand-soft text-brand-soft-ink" aria-hidden="true">
              <copy.icon className="size-5" />
            </span>
            <span>{copy.title}</span>
          </h2>
          <span className={`shrink-0 text-[11px] font-bold px-2.5 py-0.5 rounded-full border ${badge.style}`}>
            {badge.text}
          </span>
        </div>
        <p className="text-xs text-muted leading-relaxed">{copy.about}</p>
        <p className="text-xs text-ink-2">
          <span className="font-semibold">{t("يعمل الآن على: ", "Running on: ")}</span>
          {inUse.provider ? (
            <>
              <span dir="ltr" className="font-mono">{providerName(inUse.provider)} · {inUse.model_id}</span>
              <span> · {keyLabel}</span>
            </>
          ) : isVoice ? (
            <span>{t("لا شيء (رسالةً برسالة)", "nothing (message by message)")}</span>
          ) : (
            <span className="font-semibold text-warning-ink">
              {t("لا يوجد مفتاح. أضف مفتاحاً واحفظ.", "No key yet. Add one and save.")}
            </span>
          )}
        </p>
        {purpose === "embedding" && inUse.provider && (
          <p className="text-xs text-ink-2">
            {section.indexed_documents
              ? t(`✓ المنيو مفهرس (${section.indexed_documents} عنصر).`, `✓ Menu indexed (${section.indexed_documents} items).`)
              : t(
                  "المنيو لم يُفهرس بعد بهذا الموديل؛ يُفهرس تلقائياً عند الحفظ أو عند أول بحث.",
                  "The menu isn't indexed with this model yet; it's indexed on save or on the first search.",
                )}
          </p>
        )}
      </header>

      <form onSubmit={save} className="space-y-3.5 border-t border-line pt-4">
        {section.providers.length > 1 && (
          <div>
            <p id={`${purpose}-provider-label`} className={labelClass}>{t("المزوّد", "Provider")}</p>
            <div className="grid grid-cols-2 gap-1 rounded-2xl border border-line bg-surface-3/70 p-1" role="group" aria-labelledby={`${purpose}-provider-label`}>
              {section.providers.map((provider) => (
                <button
                  key={provider}
                  type="button"
                  aria-pressed={form.provider === provider}
                  onClick={() => setForm(formFor(section, provider))}
                  className={`relative isolate flex h-10 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition-colors ${
                    form.provider === provider ? "text-ink" : "text-muted hover:text-ink"
                  }`}
                >
                  {form.provider === provider && (
                    <motion.span layoutId={`provider-${purpose}`} className="absolute inset-0 -z-10 rounded-xl border border-line bg-surface shadow-card" transition={spring.snappy} />
                  )}
                  {providerName(provider)}
                  {section.saved[provider]?.has_api_key && <KeyRound className="size-3.5 text-success" aria-label={t("مفتاح محفوظ", "Key saved")} />}
                </button>
              ))}
            </div>
          </div>
        )}

        <div>
          <label htmlFor={`${purpose}-model`} className={labelClass}>{t("الموديل (Model ID)", "Model ID")}</label>
          <input
            id={`${purpose}-model`}
            required
            maxLength={150}
            value={form.model_id}
            onChange={(event) => setForm({ ...form, model_id: event.target.value })}
            placeholder={section.suggested_models[form.provider]}
            dir="ltr"
            className={inputClass}
          />
        </div>

        <div>
          <label htmlFor={`${purpose}-key`} className={labelClass}>
            {t(`مفتاح API (${providerName(form.provider)})`, `API key (${providerName(form.provider)})`)}
          </label>
          <div className="flex items-center gap-2">
            <input
              id={`${purpose}-key`}
              name={`${purpose}-api-key`}
              type={showKey ? "text" : "password"}
              autoComplete="new-password"
              required={keyNeeded}
              maxLength={512}
              value={form.api_key}
              onChange={(event) => setForm({ ...form, api_key: event.target.value })}
              placeholder={keyPlaceholder}
              dir="ltr"
              className={`${inputClass} flex-1 min-w-0`}
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="shrink-0 text-xs px-3 py-2.5 rounded-xl border border-line-strong bg-surface hover:bg-surface-3 text-muted transition-colors"
            >
              {showKey ? t("إخفاء", "Hide") : t("إظهار", "Show")}
            </button>
          </div>
          <p className="mt-1 text-[11px] text-muted">
            🔒 {t("يُحفظ مشفّراً ولا يظهر مرة أخرى.", "Stored encrypted and never shown again.")}
          </p>
        </div>

        {purpose === "chat" && (
          <div>
            <label htmlFor="chat-thinking" className={labelClass}>{t("مستوى التفكير", "Thinking level")}</label>
            <SelectField
              id="chat-thinking"
              ariaLabel={t("مستوى التفكير", "Thinking level")}
              value={form.thinking}
              onValueChange={(value) => setForm({ ...form, thinking: value })}
              options={[
                { value: "", label: t("افتراضي الموديل", "Model default") },
                ...REASONING_LEVELS[form.provider].map((level) => ({ value: level, label: level })),
              ]}
            />
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            type="submit"
            disabled={busy !== null}
            aria-busy={busy === "save"}
            className="inline-flex flex-1 min-w-[8rem] items-center justify-center gap-2 rounded-xl bg-brand hover:bg-brand-hover text-on-brand py-2.5 text-xs sm:text-sm font-bold shadow-xs transition-all disabled:opacity-50"
          >
            {busy === "save" && <ButtonSpinner />}
            {busy === "save"
              ? t("جاري الحفظ...", "Saving...")
              : isVoice && !voiceOn
                ? t("تشغيل وحفظ", "Switch on")
                : t("حفظ", "Save")}
          </button>
          <button
            type="button"
            onClick={test}
            disabled={busy !== null}
            aria-busy={busy === "test"}
            className="inline-flex flex-1 min-w-[8rem] items-center justify-center gap-2 rounded-xl border border-brand-line text-brand hover:bg-brand-soft py-2.5 text-xs sm:text-sm font-bold transition-all disabled:opacity-50"
          >
            {busy === "test" && <ButtonSpinner />}
            {busy === "test" ? t("جاري الفحص...", "Testing...") : t("فحص الاتصال", "Test connection")}
          </button>
          {voiceOn && (
            <button
              type="button"
              onClick={switchOff}
              disabled={busy !== null}
              aria-busy={busy === "off"}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-danger/25 text-danger-ink hover:bg-danger-soft px-4 py-2.5 text-xs sm:text-sm font-bold transition-all disabled:opacity-50"
            >
              {busy === "off" && <ButtonSpinner />}
              {busy === "off" ? t("جاري الإيقاف...", "Switching off...") : t("إيقاف", "Switch off")}
            </button>
          )}
        </div>

        <AnimatePresence>
          {feedback && (
            <motion.p
              key={feedback.text}
              role="status"
              aria-live="polite"
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0, x: feedback.error ? [0, -6, 6, -3, 3, 0] : 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35 }}
              className={`rounded-2xl border px-3.5 py-2.5 text-sm font-semibold ${
                feedback.error
                  ? "bg-danger-soft border-danger/25 text-danger-ink"
                  : "bg-success-soft border-success/25 text-success-ink"
              }`}
            >
              {feedback.text}
            </motion.p>
          )}
        </AnimatePresence>
      </form>
    </motion.section>
  );
}
