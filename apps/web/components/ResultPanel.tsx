"use client";

import { api } from "@/lib/api";
import { formatBytes, formatDuration, formatNumber } from "@/lib/format";
import type { GenerationResult } from "@/types";

import { Button, Stat, cx } from "./Primitives";

const CHECK_LABELS: Record<string, string> = {
  notes_present: "Map data valid",
  audio_present: "Audio present",
  cover_present: "Cover image present",
  positions_valid: "Note positions valid",
  directions_valid: "Cut directions valid",
  beats_sorted: "Note ordering valid",
  beats_non_negative: "Timing valid",
  no_duplicate_notes: "No duplicate notes",
  no_simultaneous_same_hand: "No same-hand collisions",
  same_hand_spacing_ok: "Same-hand spacing playable",
  obstacles_valid: "Wall geometry valid",
  obstacles_do_not_block_notes: "Walls clear of notes",
  parity_ok: "Parity checks passed",
  peak_nps_within_profile: "Difficulty profile respected",
  average_nps_reasonable: "Density reasonable",
  hands_balanced: "Hands balanced",
};

export function ResultPanel({
  jobId,
  result,
  onAnotherDifficulty,
  onReseed,
  busy,
}: {
  jobId: string;
  result: GenerationResult;
  onAnotherDifficulty: () => void;
  onReseed: () => void;
  busy: boolean;
}) {
  const stats = result.statistics;
  const checks = Object.entries(result.validation.checks);

  return (
    <div className="space-y-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.22em] text-neon-lime">
            Map Ready
          </p>
          <h2 className="mt-1.5 text-2xl font-bold tracking-tight text-slate-50">
            {result.song.title}
          </h2>
          <p className="mt-0.5 text-sm text-slate-400">
            {result.song.artist} · mapped by {result.song.mapper}
          </p>
        </div>
        <div className="rounded-xl border border-neon-violet/40 bg-neon-violet/10 px-4 py-2 text-center">
          <div className="label text-neon-violet/80">Difficulty</div>
          <div className="mt-0.5 text-lg font-bold text-slate-50">
            {result.difficulty_label}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <Stat label="Notes" value={String(stats.total_notes)} accent />
        <Stat label="Average NPS" value={formatNumber(stats.average_nps)} accent />
        <Stat label="Song NPS" value={formatNumber(stats.song_nps)} />
        <Stat label="Peak NPS" value={formatNumber(stats.peak_nps)} />
        <Stat label="Max local NPS" value={formatNumber(stats.max_local_nps)} />
        <Stat
          label={result.variable_tempo ? "Base BPM" : "BPM"}
          value={
            result.variable_tempo
              ? `${formatNumber(result.bpm, 1)} (${result.tempo_segment_count}×)`
              : formatNumber(result.bpm, 1)
          }
        />
        <Stat label="Duration" value={formatDuration(result.duration)} />
        <Stat label="Walls" value={String(stats.total_walls)} />
        <Stat label="Lighting events" value={String(stats.total_lights)} />
        <Stat label="Left / Right" value={`${stats.left_notes} / ${stats.right_notes}`} />
        <Stat label="Doubles" value={String(stats.doubles)} />
        <Stat label="Crossovers" value={String(stats.crossovers)} />
        <Stat label="Note jump speed" value={formatNumber(result.note_jump_speed, 1)} />
        <Stat label="Style" value={result.style} />
        <Stat label="Intensity" value={result.intensity.toFixed(2)} />
        <Stat label="Seed" value={String(result.seed)} />
        <Stat label="Plays like" value={stats.estimated_difficulty} />
      </div>

      <div className="rule" />

      <div>
        <p className="label mb-3">Validation</p>
        <ul className="grid gap-1.5 sm:grid-cols-2">
          {checks.map(([key, passed]) => (
            <li
              key={key}
              className={cx(
                "flex items-center gap-2 text-sm",
                passed ? "text-slate-300" : "text-amber-300",
              )}
            >
              <span aria-hidden className={passed ? "text-neon-lime" : "text-neon-amber"}>
                {passed ? "✓" : "!"}
              </span>
              {CHECK_LABELS[key] ?? key.replace(/_/g, " ")}
            </li>
          ))}
        </ul>

        {result.validation.warnings.length > 0 && (
          <ul className="mt-4 space-y-1.5 rounded-xl border border-neon-amber/25 bg-neon-amber/[0.06] p-4 text-sm text-amber-200">
            {result.validation.warnings.map((warning, index) => (
              <li key={index} className="leading-relaxed">
                {warning}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rule" />

      <div className="space-y-3">
        <a
          href={api.downloadUrl(jobId)}
          download
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-neon-cyan to-neon-blue px-5 py-4 text-sm font-bold tracking-wide text-ink-950 shadow-glow transition hover:brightness-110 active:scale-[0.985]"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M12 3v12" />
            <path d="m7 10 5 5 5-5" />
            <path d="M5 21h14" />
          </svg>
          Download Beat Saber Map
        </a>
        <p className="text-center text-xs text-slate-500">
          {result.package.filename} · {formatBytes(result.package.size_bytes)} ·{" "}
          {result.package.contents.join(", ")}
        </p>

        <div className="grid gap-2.5 sm:grid-cols-2">
          <Button variant="secondary" onClick={onAnotherDifficulty} disabled={busy}>
            Generate Another Difficulty
          </Button>
          <Button variant="secondary" onClick={onReseed} disabled={busy} loading={busy}>
            Generate Again With New Seed
          </Button>
        </div>
        <p className="text-center text-xs text-slate-500">
          Both reuse the analysis that has already been done — the song is not re-processed.
        </p>
      </div>
    </div>
  );
}
