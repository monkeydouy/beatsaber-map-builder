"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { Job } from "@/types";

/**
 * Statuses after which the server will not move on its own.
 *
 * `ANALYZED` is terminal for the *analysis* stage but not for the job: the user
 * can start a generation from it, which is why callers must be able to restart
 * polling rather than relying on the job id changing.
 */
const TERMINAL: ReadonlySet<string> = new Set(["ANALYZED", "COMPLETED", "FAILED"]);

const POLL_INTERVAL_MS = 1200;
const MAX_CONSECUTIVE_FAILURES = 10;

/**
 * Polls the backend for a job's real state.
 *
 * Progress shown to the user always comes from here — never from a local
 * timer — so the bar reflects what the server is actually doing.
 */
export function useJob(jobId: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  // Bumping this restarts the poll loop for the same job, which is what
  // happens when a second stage (generation) begins after analysis settled.
  const [pollNonce, setPollNonce] = useState(0);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    clearTimer();

    if (!jobId) {
      setJob(null);
      setPollError(null);
      return;
    }

    let failures = 0;

    const poll = async () => {
      try {
        const next = await api.job(jobId);
        if (cancelled) return;
        failures = 0;
        setPollError(null);
        setJob(next);
        if (!TERMINAL.has(next.status)) {
          timer.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (error) {
        if (cancelled) return;
        failures += 1;
        // A couple of dropped polls is normal; only surface a persistent one.
        if (failures >= 3) {
          setPollError(
            error instanceof ApiError ? error.message : "Lost contact with the server.",
          );
        }
        if (failures < MAX_CONSECUTIVE_FAILURES) {
          timer.current = setTimeout(poll, POLL_INTERVAL_MS * Math.min(failures, 4));
        }
      }
    };

    void poll();

    return () => {
      cancelled = true;
      clearTimer();
    };
  }, [jobId, pollNonce, clearTimer]);

  /**
   * Resume polling after starting new server-side work on the same job.
   * Without this the loop would stay stopped, because it halted on a status
   * that was terminal at the time.
   */
  const restart = useCallback(() => setPollNonce((value) => value + 1), []);

  return { job, pollError, restart, setJob };
}
