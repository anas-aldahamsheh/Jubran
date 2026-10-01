"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

export type SelectOption = { value: string; label: string; disabled?: boolean };

type SelectFieldProps = {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  ariaLabel: string;
  /** Lets a visible <label htmlFor> point at this field. */
  id?: string;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  required?: boolean;
};

type MenuPosition = { top: number; left: number; width: number; maxHeight: number };

export function SelectField({ value, onValueChange, options, ariaLabel, id: triggerId, placeholder, className = "", disabled = false, required = false }: SelectFieldProps) {
  const id = useId().replaceAll(":", "");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const selectedIndex = options.findIndex((option) => option.value === value);
  const selected = options[selectedIndex];

  const updatePosition = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const below = window.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const desiredHeight = Math.min(320, options.length * 40 + 12);
    const openingAbove = below < Math.min(180, desiredHeight) && above > below;
    const maxHeight = Math.min(desiredHeight, Math.max(80, openingAbove ? above : below));
    setPosition({
      top: openingAbove ? Math.max(8, rect.top - maxHeight - 6) : rect.bottom + 6,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - rect.width - 8)),
      width: Math.min(rect.width, window.innerWidth - 16),
      maxHeight,
    });
  }, [options.length]);

  const openMenu = () => {
    if (disabled) return;
    setHighlighted(selectedIndex >= 0 ? selectedIndex : Math.max(0, options.findIndex((option) => !option.disabled)));
    updatePosition();
    setOpen(true);
  };

  const closeMenu = () => setOpen(false);

  const choose = (index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    onValueChange(option.value);
    closeMenu();
    triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) closeMenu();
    };
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) closeMenu();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("focusin", onFocusIn);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("focusin", onFocusIn);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;
    document.getElementById(`${id}-option-${highlighted}`)?.scrollIntoView({ block: "nearest" });
  }, [highlighted, id, open]);

  const moveHighlight = (direction: 1 | -1) => {
    if (!options.length) return;
    let index = highlighted;
    for (let count = 0; count < options.length; count += 1) {
      index = (index + direction + options.length) % options.length;
      if (!options[index].disabled) { setHighlighted(index); return; }
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openMenu();
      else moveHighlight(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Home" && open) {
      event.preventDefault();
      setHighlighted(Math.max(0, options.findIndex((option) => !option.disabled)));
    } else if (event.key === "End" && open) {
      event.preventDefault();
      const index = options.findLastIndex((option) => !option.disabled);
      if (index >= 0) setHighlighted(index);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(highlighted);
      else openMenu();
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      closeMenu();
    } else if (event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const start = open ? highlighted + 1 : 0;
      const match = [...options.slice(start), ...options.slice(0, start)].findIndex((option) =>
        !option.disabled && option.label.toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase())
      );
      if (match >= 0) {
        event.preventDefault();
        if (!open) openMenu();
        setHighlighted((start + match) % options.length);
      }
    }
  };

  return <>
    <button
      ref={triggerRef}
      id={triggerId}
      type="button"
      role="combobox"
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? `${id}-listbox` : undefined}
      aria-activedescendant={open && options.length ? `${id}-option-${highlighted}` : undefined}
      aria-required={required || undefined}
      disabled={disabled}
      onClick={() => open ? closeMenu() : openMenu()}
      onKeyDown={onKeyDown}
      className={`flex min-h-[2.875rem] w-full items-center justify-between gap-3 rounded-[0.875rem] border border-line-strong bg-surface px-3.5 py-2 text-start text-[0.9375rem] text-ink shadow-hairline transition-[border-color,box-shadow] hover:border-subtle focus:outline-none focus-visible:border-brand focus-visible:shadow-[0_0_0_4px_var(--ring)] disabled:cursor-not-allowed disabled:opacity-60 ${open ? "border-brand shadow-[0_0_0_4px_var(--ring)]" : ""} ${className}`}
    >
      <span className={`min-w-0 flex-1 truncate ${selected ? "" : "text-subtle"}`}>{selected?.label ?? placeholder ?? ariaLabel}</span>
      <ChevronDown size={16} aria-hidden="true" className={`shrink-0 text-muted transition-transform duration-200 ${open ? "rotate-180 text-brand" : ""}`} />
    </button>
    {open && position && createPortal(
      <div
        ref={menuRef}
        id={`${id}-listbox`}
        role="listbox"
        aria-label={ariaLabel}
        className="fixed z-[130] origin-top animate-in fade-in zoom-in-95 overflow-y-auto rounded-2xl border border-line bg-elevated p-1.5 shadow-float"
        style={position}
        dir={document.documentElement.dir === "ltr" ? "ltr" : "rtl"}
      >
        {options.map((option, index) => <div
          key={`${option.value}-${index}`}
          id={`${id}-option-${index}`}
          role="option"
          aria-selected={option.value === value}
          aria-disabled={option.disabled || undefined}
          onMouseEnter={() => { if (!option.disabled) setHighlighted(index); }}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => choose(index)}
          className={`flex min-h-10 items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm transition-colors ${option.disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"} ${option.value === value ? "bg-brand-soft font-semibold text-brand-soft-ink" : index === highlighted ? "bg-surface-3 text-ink" : "text-ink"}`}
        >
          <span className="min-w-0 flex-1 break-words text-start">{option.label}</span>
          {option.value === value && <Check size={15} aria-hidden="true" className="shrink-0" />}
        </div>)}
      </div>, document.body
    )}
  </>;
}
