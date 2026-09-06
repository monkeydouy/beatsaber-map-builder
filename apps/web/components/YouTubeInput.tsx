"use client";

import { useMemo, useState } from "react";

import { Button, ErrorNote } from "./Primitives";

const MAX_LINKS = 8;

/** Split whatever the user pasted into candidate links. */
function parseLinks(raw: string): string[] {
  const seen = new Set<string>();
  const links: string[] = [];
  for (const token of raw.split(/[\s,]+/)) {
    const trimmed = token.trim();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      links.push(trimmed);
    }
  }
  return links;
}

export function YouTubeInput({
  onSubmit,
  error,
  starting,
}: {
  onSubmit: (urls: string[]) => void;
  error: string | null;
  starting: boolean;
}) {
  const [text, setText] = useState("");
  const [consented, setConsented] = useState(false);

  const links = useMemo(() => parseLinks(text), [text]);
  const tooMany = links.length > MAX_LINKS;
  const plural = links.length === 1 ? "video" : "videos";

  return (
    <div className="space-y-4">
      <label className="block">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={links.length > 1 ? 5 : 3}
          placeholder={"https://youtube.com/watch?v=...\nOne link per line for several songs"}
          className="field resize-y font-mono text-xs leading-relaxed"
          aria-label="YouTube video links"
          spellCheck={false}
        />
      </label>

      {links.length > 0 && (
        <p className="text-xs text-slate-500">
          {links.length} {plural} detected
          {tooMany && (
            <span className="text-amber-300">
              {" "}
              — only the first {MAX_LINKS} will be used
            </span>
          )}
        </p>
      )}

      {error && <ErrorNote>{error}</ErrorNote>}

      <label className="flex cursor-pointer items-start gap-3 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={consented}
          onChange={(event) => setConsented(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-[#22e4f2]"
        />
        <span className="leading-relaxed">
          I confirm that I have permission to process{" "}
          {links.length > 1 ? "all of this audio" : "this audio"}.
        </span>
      </label>

      <Button
        type="button"
        fullWidth
        onClick={() => onSubmit(links.slice(0, MAX_LINKS))}
        disabled={links.length === 0 || !consented || starting}
        loading={starting}
      >
        {starting
          ? "Fetching…"
          : links.length > 1
            ? `Fetch ${Math.min(links.length, MAX_LINKS)} songs`
            : "Fetch Song"}
      </Button>

      <p className="text-xs leading-relaxed text-slate-500">
        Each video is checked before anything downloads, so a private video or a
        livestream is reported rather than silently failing later. Details appear once
        they resolve.
      </p>
    </div>
  );
}
