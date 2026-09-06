"use client";

export function IntensitySlider({
  value,
  onChange,
  disabled,
}: {
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  const percent = Math.round(value * 100);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="label">Intensity</span>
        <span className="font-mono text-sm font-semibold tabular-nums text-neon-cyan">
          {value.toFixed(2)}
        </span>
      </div>

      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        disabled={disabled}
        aria-label="Intensity"
        onChange={(event) => onChange(Number(event.target.value))}
        style={{ ["--fill" as string]: `${percent}%` }}
        className="w-full cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
      />

      <div className="mt-2 flex justify-between text-xs text-slate-500">
        <span>Low</span>
        <span>High</span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-500">
        Intensity shapes the map <em>within</em> the chosen difficulty — it never pushes it
        into the next one.
      </p>
    </div>
  );
}
