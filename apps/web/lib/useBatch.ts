"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { Job } from "@/types";

const TERMINAL: ReadonlySet<string> = new Set(["ANALYZED", "COMPLETED", "FAILED"]);
const POLL_INTERVAL_MS = 2000;

/**
 * Track the analysis progress of every song in a batch.
 *
 * The active song is polled separately and more often by `useJob`; this exists
 * so the queue can show the others advancing while the user works on one of
 * them. Polling stops once every job has settled — and must be restartable,
 * because generating maps for the whole queue is new server-side work that
 * begins from exactly that settled state.
 */
export function useBatch(jobIds: string[]) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pollNonce, setPollNonce] = useState(0);

  const key = jobIds.join(",");

  useEffect(() => {
    let cancelled = false;
    if (timer.current) clearTimeout(timer.current);
    if (jobIds.length === 0) {
      setJobs({});
      return;
    }

    const poll = async () => {
      const results = await Promise.all(
        jobIds.map(async (id) => {
          try {
            return [id, await api.job(id)] as const;
          } catch {
            return [id, null] as const;
          }
        }),
      );
      if (cancelled) return;

      const next: Record<string, Job> = {};
      for (const [id, job] of results) if (job) next[id] = job;
      setJobs(next);

      const settled = jobIds.every((id) => next[id] && TERMINAL.has(next[id].status));
      if (!settled) timer.current = setTimeout(poll, POLL_INTERVAL_MS);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
    // `key` stands in for the id list; the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, pollNonce]);

  /** Resume polling after the queue has been given new work to do. */
  const restart = useCallback(() => setPollNonce((value) => value + 1), []);

  /** Merge in a job the caller already has, so the queue reflects it at once. */
  const merge = useCallback((job: Job) => {
    setJobs((current) => ({ ...current, [job.id]: job }));
  }, []);

  return { jobs, merge, restart };
}
