"use client";

import { useMemo } from "react";

import { formatDuration, humanizeSection, sectionColor } from "@/lib/format";
import type { AnalysisSummary } from "@/types";

/**
 * A compact timeline of the song: energy envelope behind, detected structure
 * in front. Purely informational — it never gates map generation.
 */
export function AnalysisVisualizer({ analysis }: { analysis: AnalysisSummary }) {
  const { path, width, height } = useMemo(() => {
    const curve = analysis.energy_curve;
    const w = 1000;
    const h = 100;
    if (curve.length < 2) return { path: "", width: w, height: h };

    const peak = Math.max(...curve, 0.0001);
    const points = curve.map((value, index) => {
      const x = (index / (curve.length - 1)) * w;
      const y = h - (value / peak) * (h - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return { path: `M0,${h} L${points.join(" L")} L${w},${h} Z`, width: w, height: h };
  }, [analysis.energy_curve]);

  const duration = analysis.duration || 1;

  return (
    <div className="space-y-3">
      <div className="relative overflow-hidden rounded-xl border border-white/[0.07] bg-ink-950/60">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          className="block h-24 w-full"
          role="img"
          aria-label="Energy over the length of the song"
        >
          <defs>
            <linearGradient id="energy-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22e4f2" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#4a7dff" stopOpacity="0.05" />
            </linearGradient>
          </defs>
          {path && <path d={path} fill="url(#energy-fill)" />}
        </svg>
      </div>

      {/* Section bar */}
      <div className="flex h-7 w-full overflow-hidden rounded-lg border border-white/[0.07]">
        {analysis.sections.map((section, index) => {
          const share = ((section.end - section.start) / duration) * 100;
          return (
            <div
              key={index}
              title={`${humanizeSection(section.type)} · ${formatDuration(section.start)}–${formatDuration(section.end)}`}
              style={{
                width: `${share}%`,
                backgroundColor: sectionColor(section.type),
                opacity: 0.35 + section.intensity * 0.55,
              }}
              className="h-full border-r border-ink-950/60 last:border-r-0"
            />
          );
        })}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-500">
        {Array.from(new Set(analysis.sections.map((section) => section.type))).map((type) => (
          <span key={type} className="inline-flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: sectionColor(type) }}
            />
            {humanizeSection(type)}
          </span>
        ))}
      </div>
    </div>
  );
}
