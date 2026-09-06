"use client";

import { formatDuration } from "@/lib/format";
import type { AnalysisSummary, SongMetadata } from "@/types";

export function SongDetails({
  metadata,
  analysis,
  title,
  artist,
  mapper,
  onTitle,
  onArtist,
  onMapper,
  disabled,
}: {
  metadata: SongMetadata;
  analysis: AnalysisSummary | null;
  title: string;
  artist: string;
  mapper: string;
  onTitle: (value: string) => void;
  onArtist: (value: string) => void;
  onMapper: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-5 sm:flex-row">
        {metadata.thumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={metadata.thumbnail}
            alt=""
            className="h-24 w-40 shrink-0 rounded-xl object-cover ring-1 ring-white/10"
          />
        ) : (
          <div
            aria-hidden
            className="flex h-24 w-40 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-neon-blue/25 to-neon-magenta/25 ring-1 ring-white/10"
          >
            <div className="flex items-end gap-[3px]">
              {[9, 16, 24, 14, 20, 11, 18].map((height, index) => (
                <span
                  key={index}
                  className="w-[3px] rounded-full bg-neon-cyan/80"
                  style={{ height }}
                />
              ))}
            </div>
          </div>
        )}

        <dl className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-3">
          <div>
            <dt className="label">Duration</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {formatDuration(analysis?.duration ?? metadata.duration)}
            </dd>
          </div>
          <div>
            <dt className="label">Detected BPM</dt>
            <dd className="mt-1 font-mono text-sm font-semibold text-neon-cyan">
              {analysis ? analysis.bpm.toFixed(1) : "—"}
              {analysis && analysis.tempo_confidence < 0.5 && (
        <div className="rounded-xl border border-neon-amber/30 bg-neon-amber/[0.07] px-4 py-3 text-sm text-amber-200">
          <p className="font-medium">
            The tempo is uncertain ({Math.round(analysis.tempo_confidence * 100)}%
            confidence).
          </p>
          <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
            If the notes feel out of time, this is why. Set the right BPM under{" "}
            <span className="font-medium">Advanced settings → Tempo override</span> —
            it re-fits instantly, no re-upload.
            {analysis.tempo_candidates && analysis.tempo_candidates.length > 1 && (
              <>
                {" "}Other tempos that scored well:{" "}
                {analysis.tempo_candidates
                  .slice(1, 4)
                  .map((candidate) => candidate.bpm.toFixed(1))
                  .join(", ")}
                .
              </>
            )}
          </p>
        </div>
      )}

      {analysis?.variable_tempo && (
                <span className="ml-1 text-[0.65rem] font-normal text-neon-amber">
                  +{analysis.tempo_segments.length - 1}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="label">Time signature</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {analysis ? analysis.meter : "—"}
              {analysis?.triple_subdivision && (
                <span className="ml-1.5 text-[0.65rem] font-normal text-neon-violet">
                  shuffle
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="label">Confidence</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {analysis ? `${Math.round(analysis.tempo_confidence * 100)}%` : "—"}
            </dd>
          </div>
          <div>
            <dt className="label">Beats</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {analysis?.beat_count ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="label">Onsets</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {analysis?.onset_count ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="label">Sections</dt>
            <dd className="mt-1 font-mono text-sm text-slate-100">
              {analysis?.sections.length ?? "—"}
            </dd>
          </div>
        </dl>
      </div>

      {analysis && analysis.tempo_confidence < 0.5 && (
        <div className="rounded-xl border border-neon-amber/30 bg-neon-amber/[0.07] px-4 py-3 text-sm text-amber-200">
          <p className="font-medium">
            The tempo is uncertain ({Math.round(analysis.tempo_confidence * 100)}%
            confidence).
          </p>
          <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
            If the notes feel out of time, this is why. Set the right BPM under{" "}
            <span className="font-medium">Advanced settings → Tempo override</span> —
            it re-fits instantly, no re-upload.
            {analysis.tempo_candidates && analysis.tempo_candidates.length > 1 && (
              <>
                {" "}Other tempos that scored well:{" "}
                {analysis.tempo_candidates
                  .slice(1, 4)
                  .map((candidate) => candidate.bpm.toFixed(1))
                  .join(", ")}
                .
              </>
            )}
          </p>
        </div>
      )}

      {analysis?.variable_tempo && (
        <div className="rounded-xl border border-neon-amber/25 bg-neon-amber/[0.06] px-4 py-3 text-sm text-amber-200">
          <p className="font-medium">This song does not hold a steady tempo.</p>
          <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
            The map follows the performance instead of averaging it —{" "}
            {analysis.tempo_segments.map((segment) => segment.bpm.toFixed(1)).join(" → ")} BPM.
            Beat Saber handles this natively, so no extra mods are needed.
          </p>
        </div>
      )}

      <div className="rule" />

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="label">Song Title</span>
          <input
            className="field mt-1.5"
            value={title}
            disabled={disabled}
            onChange={(event) => onTitle(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Artist</span>
          <input
            className="field mt-1.5"
            value={artist}
            disabled={disabled}
            onChange={(event) => onArtist(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="label">Mapper</span>
          <input
            className="field mt-1.5"
            value={mapper}
            disabled={disabled}
            onChange={(event) => onMapper(event.target.value)}
          />
        </label>
      </div>
    </div>
  );
}
