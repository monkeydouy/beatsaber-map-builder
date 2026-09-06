"use client";

import { useState } from "react";

import { cx } from "./Primitives";

export function AdvancedSettings({
  bpm,
  onBpm,
  detectedBpm,
  tempoCandidates,
  seed,
  onSeed,
  enableWalls,
  onEnableWalls,
  enableLighting,
  onEnableLighting,
  enableBombs,
  onEnableBombs,
  disabled,
}: {
  bpm: string;
  onBpm: (value: string) => void;
  detectedBpm?: number;
  tempoCandidates?: Array<{ bpm: number; score: number }>;
  seed: string;
  onSeed: (value: string) => void;
  enableWalls: boolean;
  onEnableWalls: (value: boolean) => void;
  enableLighting: boolean;
  onEnableLighting: (value: boolean) => void;
  enableBombs: boolean;
  onEnableBombs: (value: boolean) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  const toggles: Array<[string, boolean, (value: boolean) => void]> = [
    ["Walls", enableWalls, onEnableWalls],
    ["Lighting", enableLighting, onEnableLighting],
    ["Bombs", enableBombs, onEnableBombs],
  ];

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-slate-300 transition hover:text-slate-100"
      >
        Advanced settings
        <span
          aria-hidden
          className={cx("text-slate-500 transition-transform", open && "rotate-180")}
        >
          ▾
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-white/[0.06] px-4 py-4">
          <label className="block">
            <span className="label">Tempo override (optional)</span>
            <input
              className="field mt-1.5"
              inputMode="decimal"
              value={bpm}
              disabled={disabled}
              placeholder={
                detectedBpm ? `Detected ${detectedBpm.toFixed(1)} BPM` : "Detected BPM"
              }
              onChange={(event) => onBpm(event.target.value.replace(/[^0-9.]/g, ""))}
            />
            <span className="mt-1.5 block text-xs leading-relaxed text-slate-500">
              If the notes feel out of time, the detected tempo is probably wrong.
              Set the right one here — the song is not re-analyzed, so this is instant.
            </span>
            {detectedBpm && (
              <span className="mt-2 flex flex-wrap gap-1.5">
                {Array.from(
                  new Set(
                    [detectedBpm / 2, detectedBpm, detectedBpm * 2, detectedBpm * 1.5]
                      .filter((value) => value >= 40 && value <= 300)
                      .map((value) => Math.round(value * 10) / 10),
                  ),
                ).map((value) => (
                  <button
                    key={value}
                    type="button"
                    disabled={disabled}
                    onClick={() => onBpm(String(value))}
                    className={cx(
                      "rounded-lg border px-2.5 py-1 font-mono text-xs transition",
                      Number(bpm) === value
                        ? "border-neon-cyan/60 bg-neon-cyan/10 text-neon-cyan"
                        : "border-white/10 text-slate-400 hover:border-white/25 hover:text-slate-200",
                    )}
                  >
                    {value}
                  </button>
                ))}
              </span>
            )}
          </label>

          <label className="block">
            <span className="label">Seed (optional)</span>
            <input
              className="field mt-1.5"
              inputMode="numeric"
              value={seed}
              disabled={disabled}
              placeholder="Leave blank for a random seed"
              onChange={(event) => onSeed(event.target.value.replace(/[^0-9]/g, ""))}
            />
            <span className="mt-1.5 block text-xs text-slate-500">
              The same seed with the same settings always produces the same map.
            </span>
          </label>

          <div className="flex flex-wrap gap-4">
            {toggles.map(([label, value, setValue]) => (
              <label
                key={label}
                className="flex cursor-pointer items-center gap-2 text-sm text-slate-300"
              >
                <input
                  type="checkbox"
                  checked={value}
                  disabled={disabled}
                  onChange={(event) => setValue(event.target.checked)}
                  className="h-4 w-4 accent-[#22e4f2]"
                />
                {label}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
