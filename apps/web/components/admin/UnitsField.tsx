"use client";

import { Plus, X } from "lucide-react";
import { useLanguage } from "@/context/LanguageContext";
import { MAX_UNIT_LENGTH, MAX_UNITS, cleanUnits, unitSuggestions } from "@/lib/units";

/**
 * How a dish is counted (plate, glass, box...): one unit, or several when it is served more
 * than one way at the same price. Left empty, the server picks it from the dish's name and
 * category. The text being typed is kept by the form, so saving includes it too.
 */
export function UnitsField({ id, units, onChange, typed, onTypedChange, automatic }: {
  id: string;
  units: string[];
  onChange: (units: string[]) => void;
  typed: string;
  onTypedChange: (text: string) => void;
  /** The unit picked automatically, when known (a dish already saved without units). */
  automatic?: string | null;
}) {
  const { t } = useLanguage();
  const full = units.length >= MAX_UNITS;
  const suggestions = full ? [] : unitSuggestions(typed, units);

  const add = (value: string) => {
    onChange(cleanUnits([...units, value]));
    onTypedChange("");
  };

  return (
    <div>
      <label htmlFor={id} className="field-label">{t("طريقة التقديم (الوحدة)", "Serving unit")}</label>
      <div className="flex min-h-[2.875rem] flex-wrap items-center gap-1.5 rounded-[0.875rem] border border-line bg-surface px-2 py-1.5 transition-colors focus-within:border-brand">
        {units.map((unit) => (
          <span key={unit} className="inline-flex items-center gap-1 rounded-lg bg-brand-soft py-1 pe-1 ps-2.5 text-sm font-semibold text-brand-soft-ink">
            {unit}
            <button type="button" onClick={() => onChange(units.filter((other) => other !== unit))}
              aria-label={t(`إزالة ${unit}`, `Remove ${unit}`)}
              className="flex size-6 items-center justify-center rounded-md transition-colors hover:bg-brand/15">
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </span>
        ))}
        {!full && (
          <input id={id} type="text" dir="auto" value={typed} maxLength={MAX_UNIT_LENGTH}
            onChange={(e) => onTypedChange(e.target.value)}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === "," || e.key === "،") && typed.trim()) {
                e.preventDefault();
                add(typed);
              } else if (e.key === "Enter") {
                e.preventDefault();
              } else if (e.key === "Backspace" && !typed && units.length) {
                onChange(units.slice(0, -1));
              }
            }}
            placeholder={units.length ? t("وحدة أخرى؟", "Another unit?")
              : automatic ? t(`تلقائي: ${automatic}`, `Automatic: ${automatic}`) : t("تلقائي", "Automatic")}
            className="min-w-28 flex-1 bg-transparent px-1.5 py-1 text-[0.9375rem] text-ink outline-none placeholder:text-subtle" />
        )}
      </div>
      {suggestions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {suggestions.map((unit) => (
            <button key={unit} type="button" onClick={() => add(unit)}
              className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs font-semibold text-ink-2 transition-colors hover:border-brand hover:text-brand">
              <Plus className="size-3" aria-hidden="true" />
              {unit}
            </button>
          ))}
        </div>
      )}
      <p className="mt-1.5 text-xs leading-relaxed text-subtle">
        {t("اتركها فارغة ليختارها النظام تلقائياً من اسم الطبق وتصنيفه. أضف أكثر من وحدة إذا كان الطبق يُقدَّم بأكثر من طريقة (مثل صحن وعلبة)، بنفس السعر.",
          "Leave it empty to pick it automatically from the dish's name and category. Add more than one when the dish is served more than one way (e.g. plate and box), at the same price.")}
      </p>
    </div>
  );
}
