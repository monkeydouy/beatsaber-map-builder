"use client";

import { api } from "@/lib/api";
import type { Job } from "@/types";

import { Spinner, cx } from "./Primitives";

export interface QueueEntry {
  jobId: string;
  filename: string;
  job: Job | null;
  error: string | null;
}

/** Short status word for a queued song. */
function statusOf(entry: QueueEntry): { label: string; tone: string } {
  if (entry.error) return { label: "failed", tone: "text-rose-300" };
  const job = entry.job;
  if (!job) return { label: "queued", tone: "text-slate-500" };
  if (job.status === "FAILED") return { label: "failed", tone: "text-rose-300" };
  if (job.status === "COMPLETED") return { label: "map ready", tone: "text-neon-lime" };
  if (job.status === "ANALYZED") return { label: "ready", tone: "text-neon-cyan" };
  return { label: `${job.progress}%`, tone: "text-slate-300" };
}

export function SongQueue({
  entries,
  activeId,
  onSelect,
  onGenerateAll,
  bulk,
}: {
  entries: QueueEntry[];
  activeId: string | null;
  onSelect: (jobId: string) => void;
  onGenerateAll: () => void;
  /** Progress of a whole-queue generation, or null when idle. */
  bulk: { done: number; total: number } | null;
}) {
  if (entries.length < 2) return null;

  const ready = entries.filter(
    (entry) => entry.job?.status === "ANALYZED" || entry.job?.status === "COMPLETED",
  ).length;

  const generated = entries
    .filter((entry) => entry.job?.status === "COMPLETED")
    .map((entry) => entry.jobId);

  const awaiting = entries.filter((entry) => entry.job?.status === "ANALYZED").length;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <p className="label">Your songs</p>
        <p className="font-mono text-xs text-slate-500">
          {ready}/{entries.length} analyzed
        </p>
      </div>

      <ul className="space-y-1.5">
        {entries.map((entry) => {
          const status = statusOf(entry);
          const active = entry.jobId === activeId;
          const working =
            entry.job !== null &&
            !["ANALYZED", "COMPLETED", "FAILED"].includes(entry.job.status);
          const title = entry.job?.metadata?.title || entry.filename;

          return (
            <li key={entry.jobId}>
              <button
                type="button"
                onClick={() => onSelect(entry.jobId)}
                aria-current={active}
                className={cx(
                  "flex w-full items-center gap-3 rounded-xl border px-4 py-2.5 text-left transition",
                  active
                    ? "border-neon-cyan/50 bg-neon-cyan/[0.07]"
                    : "border-white/[0.07] bg-white/[0.02] hover:border-white/20",
                )}
              >
                {working ? (
                  <Spinner className="h-3.5 w-3.5 text-neon-cyan" />
                ) : (
                  <span
                    aria-hidden
                    className={cx(
                      "h-2 w-2 shrink-0 rounded-full",
                      status.label === "map ready"
                        ? "bg-neon-lime"
                        : status.label === "ready"
                          ? "bg-neon-cyan"
                          : status.label === "failed"
                            ? "bg-neon-magenta"
                            : "bg-slate-600",
                    )}
                  />
                )}
                <span className="min-w-0 flex-1 truncate text-sm text-slate-200">
                  {title}
                </span>
                <span className={cx("font-mono text-xs tabular-nums", status.tone)}>
                  {status.label}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {(awaiting > 1 || bulk) && (
        <div className="space-y-1.5 pt-1">
          <button
            type="button"
            onClick={onGenerateAll}
            disabled={bulk !== null}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-neon-cyan/40 bg-neon-cyan/[0.08] px-5 py-3 text-sm font-semibold text-neon-cyan transition hover:bg-neon-cyan/[0.14] active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {bulk ? (
              <>
                <Spinner className="h-4 w-4" />
                Generating {bulk.done}/{bulk.total}…
              </>
            ) : (
              <>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="m13 2-9 12h7l-1 8 9-12h-7z" />
                </svg>
                Generate all {awaiting} maps
              </>
            )}
          </button>
          <p className="text-center text-xs text-slate-500">
            Uses the settings on the right for every song. Each keeps its own
            title and artist.
          </p>
        </div>
      )}

      {generated.length > 1 && (
        <div className="space-y-1.5 pt-1">
          <a
            href={api.bundleUrl(generated)}
            download
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-neon-lime/40 bg-neon-lime/[0.08] px-5 py-3 text-sm font-semibold text-neon-lime transition hover:bg-neon-lime/[0.14] active:scale-[0.99]"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M12 3v12" />
              <path d="m7 10 5 5 5-5" />
              <path d="M5 21h14" />
            </svg>
            Download all {generated.length} maps
          </a>
          <p className="text-center text-xs text-slate-500">
            One archive, a folder per map — extract it straight into your
            CustomLevels directory.
          </p>
        </div>
      )}
    </div>
  );
}
