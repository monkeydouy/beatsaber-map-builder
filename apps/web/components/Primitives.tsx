"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export { cx };

type ButtonVariant = "primary" | "secondary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  fullWidth?: boolean;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-gradient-to-r from-neon-cyan to-neon-blue text-ink-950 shadow-glow hover:brightness-110",
  secondary:
    "border border-white/12 bg-white/[0.04] text-slate-100 hover:border-neon-cyan/50 hover:bg-white/[0.08]",
  ghost: "text-slate-400 hover:text-slate-100",
};

export function Button({
  variant = "primary",
  loading = false,
  fullWidth = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold",
        "tracking-wide transition-all duration-150 active:scale-[0.985]",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100",
        VARIANTS[variant],
        fullWidth && "w-full",
        className,
      )}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cx(
        "h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
      aria-hidden
    />
  );
}

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={cx("panel p-6 sm:p-8", className)}>{children}</section>;
}

export function SectionHeading({
  step,
  title,
  hint,
}: {
  step?: string;
  title: string;
  hint?: string;
}) {
  return (
    <div className="mb-5">
      <div className="flex items-baseline gap-3">
        {step && (
          <span className="font-mono text-xs font-semibold text-neon-cyan/80">{step}</span>
        )}
        <h2 className="text-base font-semibold tracking-tight text-slate-100">{title}</h2>
      </div>
      {hint && <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{hint}</p>}
    </div>
  );
}

export function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
      <div className="label">{label}</div>
      <div
        className={cx(
          "mt-1 font-mono text-lg font-semibold tabular-nums",
          accent ? "text-neon-cyan" : "text-slate-100",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-neon-magenta/30 bg-neon-magenta/[0.08] px-4 py-3 text-sm text-rose-200"
    >
      <span aria-hidden className="mt-0.5 text-base leading-none">
        ⚠
      </span>
      <span className="leading-relaxed">{children}</span>
    </div>
  );
}
