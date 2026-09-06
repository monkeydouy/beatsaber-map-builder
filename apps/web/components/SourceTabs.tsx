"use client";

import { cx } from "./Primitives";

export type SourceTab = "upload" | "youtube";

export function SourceTabs({
  active,
  onChange,
  youtubeEnabled,
}: {
  active: SourceTab;
  onChange: (tab: SourceTab) => void;
  youtubeEnabled: boolean;
}) {
  const tabs: Array<{ id: SourceTab; label: string; disabled?: boolean }> = [
    { id: "upload", label: "Upload MP3" },
    { id: "youtube", label: "YouTube URL", disabled: !youtubeEnabled },
  ];

  return (
    <div
      role="tablist"
      aria-label="Audio source"
      className="grid grid-cols-2 gap-1.5 rounded-xl border border-white/[0.07] bg-ink-850/60 p-1.5"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          aria-selected={active === tab.id}
          disabled={tab.disabled}
          onClick={() => onChange(tab.id)}
          title={tab.disabled ? "YouTube input is unavailable on this server" : undefined}
          className={cx(
            "rounded-lg px-4 py-2.5 text-sm font-semibold transition-all",
            "disabled:cursor-not-allowed disabled:opacity-35",
            active === tab.id
              ? "bg-gradient-to-r from-neon-cyan/90 to-neon-blue/90 text-ink-950 shadow-glow"
              : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
