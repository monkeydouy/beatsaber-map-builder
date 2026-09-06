"use client";

import type { Job, JobStage } from "@/types";

import { cx } from "./Primitives";

/** The backend statuses, in the order they occur, per stage. */
const STAGE_STEPS: Record<JobStage, Array<{ status: string; label: string }>> = {
  analysis: [
    { status: "ACQUIRING_AUDIO", label: "Fetching audio" },
    { status: "NORMALIZING_AUDIO", label: "Normalizing audio" },
    { status: "ANALYZING_AUDIO", label: "Analyzing audio" },
    { status: "DETECTING_BEATS", label: "Detecting BPM and beats" },
    { status: "ANALYZING_SECTIONS", label: "Finding musical sections" },
    { status: "BUILDING_EVENT_TIMELINE", label: "Building musical timeline" },
  ],
  generation: [
    { status: "SELECTING_RHYTHM", label: "Selecting rhythm" },
    // Walls are planned before notes, so the notes can route around them.
    { status: "GENERATING_OBSTACLES", label: "Planning obstacles" },
    { status: "GENERATING_PATTERNS", label: "Generating patterns" },
    { status: "VALIDATING_PARITY", label: "Checking swing parity" },
    { status: "GENERATING_LIGHTING", label: "Generating lighting" },
    { status: "VALIDATING_MAP", label: "Validating Beat Saber map" },
    { status: "EXPORTING", label: "Exporting map" },
    { status: "PACKAGING", label: "Packaging download" },
  ],
};

export function ProgressPanel({ job }: { job: Job }) {
  const steps = STAGE_STEPS[job.stage] ?? STAGE_STEPS.analysis;
  const currentIndex = steps.findIndex((step) => step.status === job.status);
  const progress = Math.max(0, Math.min(100, job.progress));

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2.5 flex items-baseline justify-between">
          <p className="text-base font-semibold text-slate-100">{job.current_step}</p>
          <p className="font-mono text-sm font-semibold tabular-nums text-neon-cyan">
            {progress}%
          </p>
        </div>

        <div className="relative h-2.5 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-gradient-to-r from-neon-cyan to-neon-blue transition-[width] duration-500 ease-out"
            style={{ width: `${progress}%` }}
          />
          <div
            aria-hidden
            className="absolute inset-y-0 left-0 w-full -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/20 to-transparent"
          />
        </div>
      </div>

      <ol className="space-y-2">
        {steps.map((step, index) => {
          const done = currentIndex > index || job.status === "COMPLETED" || job.status === "ANALYZED";
          const active = currentIndex === index;
          return (
            <li
              key={step.status}
              className={cx(
                "flex items-center gap-3 text-sm transition-colors",
                done ? "text-slate-400" : active ? "text-slate-100" : "text-slate-600",
              )}
            >
              <span
                aria-hidden
                className={cx(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[0.6rem] font-bold",
                  done
                    ? "border-neon-cyan/50 bg-neon-cyan/15 text-neon-cyan"
                    : active
                      ? "border-neon-cyan bg-neon-cyan text-ink-950"
                      : "border-white/12 text-slate-600",
                )}
              >
                {done ? "✓" : index + 1}
              </span>
              <span className={cx(active && "font-medium")}>{step.label}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
