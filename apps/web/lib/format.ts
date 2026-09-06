/** Formatting helpers shared across the result and detail panels. */

export function formatDuration(seconds: number | undefined | null): string {
  if (!seconds || !Number.isFinite(seconds)) return "--:--";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatNumber(value: number, digits = 2): string {
  return Number.isFinite(value) ? value.toFixed(digits) : "--";
}

/** Turn `pre_chorus` into `Pre Chorus` for display. */
export function humanizeSection(type: string): string {
  return type
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export const SECTION_COLORS: Record<string, string> = {
  intro: "#4a7dff",
  verse: "#22e4f2",
  pre_chorus: "#a855f7",
  chorus: "#ff3d81",
  buildup: "#ffb020",
  drop: "#ff3d81",
  break: "#3d445f",
  bridge: "#a855f7",
  outro: "#4a7dff",
  low_energy: "#3d445f",
  medium_energy: "#22e4f2",
  high_energy: "#ff3d81",
  transition: "#ffb020",
};

export function sectionColor(type: string): string {
  return SECTION_COLORS[type] ?? "#22e4f2";
}
