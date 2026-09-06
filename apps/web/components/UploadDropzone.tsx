"use client";

import { useCallback, useRef, useState } from "react";

import { Button, ErrorNote, cx } from "./Primitives";

const MAX_MB = 50;
const MAX_FILES = 15;
const ACCEPTED = [".mp3", ".wav", ".ogg", ".m4a", ".flac"];

export function UploadDropzone({
  onFiles,
  busy,
}: {
  onFiles: (files: File[]) => void;
  busy: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<File[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  /** Validate locally so obvious mistakes never cost a round trip. */
  const accept = useCallback(
    (incoming: FileList | null) => {
      const files = Array.from(incoming ?? []);
      if (files.length === 0) return;
      if (files.length > MAX_FILES) {
        setError(`Please choose at most ${MAX_FILES} files at a time.`);
        return;
      }

      const rejected: string[] = [];
      const good: File[] = [];
      for (const file of files) {
        const extension = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
        if (!ACCEPTED.includes(extension)) {
          rejected.push(`${file.name} (unsupported type)`);
        } else if (file.size > MAX_MB * 1024 * 1024) {
          rejected.push(`${file.name} (over ${MAX_MB} MB)`);
        } else {
          good.push(file);
        }
      }

      // Take what we can rather than refusing the whole selection.
      setError(rejected.length ? `Skipped ${rejected.join(", ")}` : null);
      if (good.length === 0) return;
      setSelected(good);
      onFiles(good);
    },
    [onFiles],
  );

  return (
    <div className="space-y-4">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!busy) accept(event.dataTransfer.files);
        }}
        className={cx(
          "relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-12 text-center transition-all",
          busy && "pointer-events-none opacity-50",
          dragging
            ? "border-neon-cyan bg-neon-cyan/[0.07] shadow-glow"
            : "border-white/12 bg-ink-850/40 hover:border-white/25",
        )}
      >
        <div
          aria-hidden
          className={cx(
            "mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border transition-colors",
            dragging
              ? "border-neon-cyan/60 bg-neon-cyan/10 text-neon-cyan"
              : "border-white/10 bg-white/[0.03] text-slate-400",
          )}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 16V4" />
            <path d="m7 9 5-5 5 5" />
            <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
        </div>

        <p className="text-base font-semibold text-slate-100">
          {selected.length === 0
            ? "Drop your MP3s here"
            : selected.length === 1
              ? selected[0].name
              : `${selected.length} songs selected`}
        </p>
        <p className="mt-1 text-sm text-slate-500">
          {selected.length > 0
            ? `${(
                selected.reduce((sum, file) => sum + file.size, 0) /
                1024 /
                1024
              ).toFixed(1)} MB total`
            : `or choose files · up to ${MAX_FILES}, max ${MAX_MB} MB each`}
        </p>

        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED.join(",")}
          className="sr-only"
          onChange={(event) => accept(event.target.files)}
        />
        <Button
          type="button"
          variant="secondary"
          className="mt-5"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          Choose Files
        </Button>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
