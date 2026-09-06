"use client";

import type { StyleInfo, StyleValue } from "@/types";

import { cx } from "./Primitives";

export function StyleSelector({
  options,
  value,
  onChange,
  disabled,
}: {
  options: StyleInfo[];
  value: StyleValue;
  onChange: (next: StyleValue) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={cx(
              "rounded-xl border px-4 py-3 text-left transition-all",
              "disabled:cursor-not-allowed disabled:opacity-40",
              selected
                ? "border-neon-cyan/60 bg-neon-cyan/[0.08] shadow-glow"
                : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.06]",
            )}
          >
            <span
              className={cx(
                "block text-sm font-semibold",
                selected ? "text-neon-cyan" : "text-slate-200",
              )}
            >
              {option.label}
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-slate-500">
              {option.description}
            </span>
          </button>
        );
      })}
    </div>
  );
}
