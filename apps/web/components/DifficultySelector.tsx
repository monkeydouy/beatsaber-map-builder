"use client";

import type { DifficultyInfo, DifficultyValue } from "@/types";

import { cx } from "./Primitives";

/** Accent per difficulty, cool through hot as the challenge rises. */
const ACCENTS: Record<DifficultyValue, string> = {
  Easy: "from-neon-lime/80 to-emerald-400/80",
  Normal: "from-neon-cyan/85 to-sky-400/85",
  Hard: "from-neon-blue/85 to-indigo-400/85",
  Expert: "from-neon-violet/85 to-fuchsia-500/85",
  ExpertPlus: "from-neon-magenta/90 to-rose-500/90",
};

export function DifficultySelector({
  options,
  value,
  onChange,
  disabled,
}: {
  options: DifficultyInfo[];
  value: DifficultyValue;
  onChange: (next: DifficultyValue) => void;
  disabled?: boolean;
}) {
  const active = options.find((option) => option.value === value);

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
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
                "group relative overflow-hidden rounded-xl border px-3 py-3.5 text-center transition-all",
                "disabled:cursor-not-allowed disabled:opacity-40",
                selected
                  ? "border-transparent text-ink-950"
                  : "border-white/10 bg-white/[0.03] text-slate-300 hover:border-white/25 hover:bg-white/[0.06]",
              )}
            >
              {selected && (
                <span
                  aria-hidden
                  className={cx(
                    "absolute inset-0 bg-gradient-to-br",
                    ACCENTS[option.value],
                  )}
                />
              )}
              <span className="relative block text-sm font-bold tracking-wide">
                {option.label}
              </span>
              <span
                className={cx(
                  "relative mt-0.5 block font-mono text-[0.65rem] tabular-nums",
                  selected ? "text-ink-950/70" : "text-slate-500",
                )}
              >
                {option.target_nps_min}–{option.target_nps_max} NPS
              </span>
            </button>
          );
        })}
      </div>

      {active && (
        <p className="mt-3 text-sm leading-relaxed text-slate-400">{active.description}</p>
      )}
    </div>
  );
}
