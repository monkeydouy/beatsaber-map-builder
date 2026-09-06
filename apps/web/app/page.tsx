"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { AdvancedSettings } from "@/components/AdvancedSettings";
import { AnalysisVisualizer } from "@/components/AnalysisVisualizer";
import { DifficultySelector } from "@/components/DifficultySelector";
import { IntensitySlider } from "@/components/IntensitySlider";
import {
  Button,
  ErrorNote,
  Panel,
  SectionHeading,
  Spinner,
} from "@/components/Primitives";
import { ProgressPanel } from "@/components/ProgressPanel";
import { ResultPanel } from "@/components/ResultPanel";
import { SongDetails } from "@/components/SongDetails";
import { SongQueue, type QueueEntry } from "@/components/SongQueue";
import { SourceTabs, type SourceTab } from "@/components/SourceTabs";
import { StyleSelector } from "@/components/StyleSelector";
import { UploadDropzone } from "@/components/UploadDropzone";
import { YouTubeInput } from "@/components/YouTubeInput";
import { ApiError, api } from "@/lib/api";
import { useBatch } from "@/lib/useBatch";
import { useJob } from "@/lib/useJob";
import type {
  BatchItem,
  DifficultyInfo,
  DifficultyValue,
  StyleInfo,
  StyleValue,
} from "@/types";

const DEFAULT_DIFFICULTY: DifficultyValue = "Expert";
const DEFAULT_STYLE: StyleValue = "balanced";

/** Statuses that mean the server is actively working on this job. */
const BUSY_STATUSES: ReadonlySet<string> = new Set([
  "CREATED",
  "ACQUIRING_AUDIO",
  "NORMALIZING_AUDIO",
  "ANALYZING_AUDIO",
  "DETECTING_BEATS",
  "ANALYZING_SECTIONS",
  "BUILDING_EVENT_TIMELINE",
  "SELECTING_RHYTHM",
  "GENERATING_PATTERNS",
  "VALIDATING_PARITY",
  "GENERATING_OBSTACLES",
  "GENERATING_LIGHTING",
  "VALIDATING_MAP",
  "EXPORTING",
  "PACKAGING",
]);

export default function Home() {
  const [tab, setTab] = useState<SourceTab>("upload");
  const [jobId, setJobId] = useState<string | null>(null);
  const [batch, setBatch] = useState<BatchItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [difficulties, setDifficulties] = useState<DifficultyInfo[]>([]);
  const [styles, setStyles] = useState<StyleInfo[]>([]);
  const [youtubeEnabled, setYoutubeEnabled] = useState(false);
  const [serverUp, setServerUp] = useState<boolean | null>(null);

  const [previewError, setPreviewError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  /** Progress of a whole-queue generation, or null when idle. */
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);
  const [generating, setGenerating] = useState(false);
  const [showOptions, setShowOptions] = useState(false);

  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [mapper, setMapper] = useState("SaberMapper AI");
  const [difficulty, setDifficulty] = useState<DifficultyValue>(DEFAULT_DIFFICULTY);
  const [style, setStyle] = useState<StyleValue>(DEFAULT_STYLE);
  const [intensity, setIntensity] = useState(0.6);
  const [seed, setSeed] = useState("");
  const [bpmOverride, setBpmOverride] = useState("");
  const [enableWalls, setEnableWalls] = useState(true);
  const [enableLighting, setEnableLighting] = useState(true);
  const [enableBombs, setEnableBombs] = useState(false);

  const { job, pollError, restart } = useJob(jobId);

  // Ids of everything in this batch, so the queue can show the rest advancing
  // while the user configures one of them.
  const batchIds = useMemo(
    () => batch.map((item) => item.job_id).filter((id): id is string => Boolean(id)),
    [batch],
  );
  const { jobs: batchJobs, merge, restart: restartBatch } = useBatch(batchIds);

  useEffect(() => {
    if (job) merge(job);
  }, [job, merge]);

  const queue: QueueEntry[] = useMemo(
    () =>
      batch.map((item) => ({
        jobId: item.job_id ?? item.filename,
        filename: item.filename,
        job: item.job_id ? (batchJobs[item.job_id] ?? null) : null,
        error: item.error,
      })),
    [batch, batchJobs],
  );

  // -- server capabilities ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [health, difficultyList, styleList] = await Promise.all([
          api.health(),
          api.difficulties(),
          api.styles(),
        ]);
        if (cancelled) return;
        setServerUp(true);
        setYoutubeEnabled(health.youtube_enabled);
        setDifficulties(difficultyList);
        setStyles(styleList);
      } catch {
        if (!cancelled) setServerUp(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fill in the editable metadata once the server reports what it found.
  useEffect(() => {
    if (!job?.metadata) return;
    setTitle((current) => current || job.metadata.title || "");
    setArtist((current) => current || job.metadata.artist || "");
  }, [job?.metadata]);

  const busy = Boolean(job && BUSY_STATUSES.has(job.status));
  const analysed = Boolean(job?.analysis);
  const completed = job?.status === "COMPLETED" && job.result;

  // -- actions ------------------------------------------------------------

  const resetAll = useCallback(() => {
    setJobId(null);
    setBatch([]);
    setError(null);
    setPreviewError(null);
    setTitle("");
    setArtist("");
    setSeed("");
    setBpmOverride("");
    setShowOptions(false);
    setDifficulty(DEFAULT_DIFFICULTY);
    setStyle(DEFAULT_STYLE);
    setIntensity(0.6);
  }, []);

  const handleUpload = useCallback(async (files: File[]) => {
    setError(null);
    setStarting(true);
    try {
      const response = await api.uploadFiles(files);
      setBatch(response.items);
      const first = response.items.find((item) => item.job_id);
      if (!first?.job_id) {
        setError(
          response.items[0]?.error ?? "None of those files could be accepted.",
        );
        return;
      }
      setJobId(first.job_id);
      setShowOptions(false);
      if (response.rejected > 0) {
        const skipped = response.items.filter((item) => item.error);
        setError(
          `${response.rejected} file(s) skipped: ${skipped
            .map((item) => `${item.filename} — ${item.error}`)
            .join("; ")}`,
        );
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Upload failed.");
    } finally {
      setStarting(false);
    }
  }, []);

  const handleYouTube = useCallback(async (urls: string[]) => {
    if (urls.length === 0) return;
    setPreviewError(null);
    setError(null);
    setStarting(true);
    try {
      const response = await api.createYouTubeJobs(urls);
      // Reuse the same queue as uploads; a link's title stands in for a name.
      setBatch(
        response.items.map((item) => ({
          filename: item.title ?? item.url,
          job_id: item.job_id,
          status: item.job_id ? "CREATED" : null,
          error: item.error,
        })),
      );
      const first = response.items.find((item) => item.job_id);
      if (!first?.job_id) {
        setPreviewError(
          response.items.find((item) => item.error)?.error ??
            "None of those links could be used.",
        );
        return;
      }
      setJobId(first.job_id);
      setShowOptions(false);
      if (response.rejected > 0) {
        const skipped = response.items.filter((item) => item.error);
        setPreviewError(
          `${response.rejected} link(s) skipped: ${skipped
            .map((item) => item.error)
            .join("; ")}`,
        );
      }
    } catch (caught) {
      setPreviewError(
        caught instanceof ApiError ? caught.message : "Could not start those jobs.",
      );
    } finally {
      setStarting(false);
    }
  }, []);

  const runGeneration = useCallback(
    async (overrideSeed?: number | null) => {
      if (!jobId) return;
      setError(null);
      setGenerating(true);
      try {
        const parsedSeed =
          overrideSeed !== undefined
            ? overrideSeed
            : seed.trim()
              ? Number(seed.trim())
              : null;
        await api.generate(jobId, {
          title: title.trim() || undefined,
          artist: artist.trim() || undefined,
          mapper: mapper.trim() || undefined,
          difficulty,
          style,
          intensity,
          seed: parsedSeed,
          bpm: bpmOverride.trim() ? Number(bpmOverride.trim()) : null,
          enable_walls: enableWalls,
          enable_lighting: enableLighting,
          enable_bombs: enableBombs,
        });
        setShowOptions(false);
        // Analysis left the poll loop stopped on a terminal status; generation
        // is new server-side work, so polling has to be resumed explicitly.
        restart();
      } catch (caught) {
        setError(
          caught instanceof ApiError ? caught.message : "Could not start generation.",
        );
      } finally {
        setGenerating(false);
      }
    },
    [
      jobId,
      seed,
      bpmOverride,
      title,
      artist,
      mapper,
      difficulty,
      style,
      intensity,
      enableWalls,
      enableLighting,
      enableBombs,
      restart,
    ],
  );

  /**
   * Generate a map for every analysed song in the queue, with one set of
   * settings. Each song keeps its own detected title and artist — only the
   * difficulty, style and toggles are shared.
   */
  const generateAll = useCallback(async () => {
    const pending = queue.filter((entry) => entry.job?.status === "ANALYZED");
    if (pending.length === 0) return;

    setError(null);
    setBulk({ done: 0, total: pending.length });
    const payload = {
      difficulty,
      style,
      intensity,
      seed: null,
      bpm: null,
      enable_walls: enableWalls,
      enable_lighting: enableLighting,
      enable_bombs: enableBombs,
    };

    try {
      for (const [index, entry] of pending.entries()) {
        // The server runs only a couple of jobs at once and answers 429 when
        // it is full, so wait for a slot rather than dropping the song.
        for (let attempt = 0; ; attempt += 1) {
          try {
            await api.generate(entry.jobId, payload);
            break;
          } catch (caught) {
            const busy = caught instanceof ApiError && caught.status === 429;
            if (!busy || attempt >= 90) throw caught;
            await new Promise((resolve) => setTimeout(resolve, 1500));
          }
        }
        setBulk({ done: index + 1, total: pending.length });
      }
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not generate maps for the whole queue.",
      );
    } finally {
      setBulk(null);
      // Both loops stopped on a settled status; the work above is new.
      restartBatch();
      restart();
    }
  }, [
    queue,
    difficulty,
    style,
    intensity,
    enableWalls,
    enableLighting,
    enableBombs,
    restart,
    restartBatch,
  ]);

  const selectSong = useCallback(
    (nextId: string) => {
      if (nextId === jobId) return;
      setJobId(nextId);
      setTitle("");
      setArtist("");
      setShowOptions(false);
      setError(null);
      setBpmOverride("");
    },
    [jobId],
  );

  const activeDifficulty = useMemo(
    () => difficulties.find((item) => item.value === difficulty),
    [difficulties, difficulty],
  );

  // -- render -------------------------------------------------------------

  const showConfigure = analysed && (!completed || showOptions);

  return (
    <main className="mx-auto w-full max-w-3xl px-5 py-12 sm:py-16">
      <header className="mb-10 text-center">
        <h1 className="bg-gradient-to-r from-neon-cyan via-slate-100 to-neon-magenta bg-clip-text text-4xl font-black tracking-tight text-transparent sm:text-5xl">
          SaberMapper AI
        </h1>
        <p className="mt-3 text-base text-slate-400">
          Turn your music into a playable Beat Saber map.
        </p>
      </header>

      {serverUp === false && (
        <div className="mb-6">
          <ErrorNote>
            Could not reach the SaberMapper API. Start the backend, then reload this page.
          </ErrorNote>
        </div>
      )}

      <div className="space-y-5">
        {/* ---------------- Step 1: source ---------------- */}
        {!jobId && (
          <Panel className="animate-fade-up">
            <SectionHeading
              step="01"
              title="Choose your music"
              hint="Upload an audio file, or point us at a YouTube video you have the rights to use."
            />
            <SourceTabs active={tab} onChange={setTab} youtubeEnabled={youtubeEnabled} />

            <div className="mt-5">
              {tab === "upload" ? (
                <UploadDropzone onFiles={handleUpload} busy={starting} />
              ) : (
                <YouTubeInput
                  onSubmit={handleYouTube}
                  error={previewError}
                  starting={starting}
                />
              )}
            </div>

            {error && <div className="mt-4">
              <ErrorNote>{error}</ErrorNote>
            </div>}
          </Panel>
        )}

        {queue.length > 1 && (
          <Panel className="animate-fade-up">
            <SongQueue
              entries={queue}
              activeId={jobId}
              onSelect={selectSong}
              onGenerateAll={generateAll}
              bulk={bulk}
            />
          </Panel>
        )}

        {/* ---------------- Progress ---------------- */}
        {job && busy && (
          <Panel className="animate-fade-up">
            <SectionHeading
              step={job.stage === "analysis" ? "02" : "04"}
              title={job.stage === "analysis" ? "Analyzing your song" : "Generating your map"}
              hint="Progress comes straight from the server as each stage completes."
            />
            <ProgressPanel job={job} />
          </Panel>
        )}

        {/* ---------------- Failure ---------------- */}
        {job?.status === "FAILED" && (
          <Panel className="animate-fade-up">
            <ErrorNote>{job.error ?? "Something went wrong."}</ErrorNote>
            <div className="mt-5 flex gap-2.5">
              <Button variant="secondary" onClick={resetAll}>
                Start over
              </Button>
              {analysed && (
                <Button onClick={() => void runGeneration()} loading={generating}>
                  Try generating again
                </Button>
              )}
            </div>
          </Panel>
        )}

        {/* ---------------- Song details + analysis ---------------- */}
        {job && analysed && !busy && (
          <Panel className="animate-fade-up">
            <SectionHeading step="02" title="Your song" />
            <SongDetails
              metadata={job.metadata}
              analysis={job.analysis}
              title={title}
              artist={artist}
              mapper={mapper}
              onTitle={setTitle}
              onArtist={setArtist}
              onMapper={setMapper}
              disabled={generating}
            />
            {job.analysis && (
              <div className="mt-6">
                <AnalysisVisualizer analysis={job.analysis} />
              </div>
            )}
          </Panel>
        )}

        {/* ---------------- Step 3: configure ---------------- */}
        {showConfigure && !busy && job?.status !== "FAILED" && (
          <Panel className="animate-fade-up">
            <SectionHeading
              step="03"
              title="Shape the map"
              hint="Difficulty sets the boundaries. Style and intensity shape the map inside them."
            />

            <div className="space-y-7">
              <div>
                <p className="label mb-3">Difficulty</p>
                <DifficultySelector
                  options={difficulties}
                  value={difficulty}
                  onChange={setDifficulty}
                  disabled={generating}
                />
              </div>

              <div>
                <p className="label mb-3">Mapping style</p>
                <StyleSelector
                  options={styles}
                  value={style}
                  onChange={setStyle}
                  disabled={generating}
                />
              </div>

              <IntensitySlider
                value={intensity}
                onChange={setIntensity}
                disabled={generating}
              />

              <AdvancedSettings
                bpm={bpmOverride}
                onBpm={setBpmOverride}
                detectedBpm={job?.analysis?.bpm}
                seed={seed}
                onSeed={setSeed}
                enableWalls={enableWalls}
                onEnableWalls={setEnableWalls}
                enableLighting={enableLighting}
                onEnableLighting={setEnableLighting}
                enableBombs={enableBombs}
                onEnableBombs={setEnableBombs}
                disabled={generating}
              />

              {error && <ErrorNote>{error}</ErrorNote>}

              <Button
                fullWidth
                onClick={() => void runGeneration()}
                loading={generating}
                disabled={!activeDifficulty}
                className="py-4 text-base"
              >
                Generate Beatmap
              </Button>
            </div>
          </Panel>
        )}

        {/* ---------------- Result ---------------- */}
        {completed && job?.result && !showOptions && (
          <Panel className="animate-fade-up">
            <ResultPanel
              jobId={job.id}
              result={job.result}
              busy={generating}
              onAnotherDifficulty={() => setShowOptions(true)}
              onReseed={() => void runGeneration(null)}
            />
          </Panel>
        )}

        {jobId && !busy && (
          <div className="flex justify-center pt-2">
            <Button variant="ghost" onClick={resetAll}>
              {queue.length > 1 ? "Start over with different songs" : "Start over with a different song"}
            </Button>
          </div>
        )}

        {pollError && (
          <div className="flex items-center justify-center gap-2 text-sm text-slate-500">
            <Spinner className="h-3 w-3" />
            {pollError}
          </div>
        )}
      </div>

      <footer className="mt-14 space-y-2 text-center text-xs leading-relaxed text-slate-600">
        <p>
          Generated maps are standard Beat Saber custom levels — no Chroma, Noodle Extensions
          or Mapping Extensions required.
        </p>
        <p>
          Only process audio you have the rights to. SaberMapper AI is not affiliated with
          Beat Games or Beat Saber.
        </p>
      </footer>
    </main>
  );
}
