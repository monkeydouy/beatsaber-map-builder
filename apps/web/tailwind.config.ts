import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#06070d",
          900: "#0a0c15",
          850: "#0f111c",
          800: "#141726",
          700: "#1d2133",
          600: "#2a2f47",
          500: "#3d445f",
        },
        neon: {
          cyan: "#22e4f2",
          blue: "#4a7dff",
          magenta: "#ff3d81",
          violet: "#a855f7",
          lime: "#8bf24a",
          amber: "#ffb020",
        },
      },
      fontFamily: {
        // A concrete system stack, not a CSS variable: an undefined var() makes
        // the whole font-family declaration invalid, which silently drops the
        // page back to the browser's default serif.
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "SF Mono",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      boxShadow: {
        glow: "0 0 24px -4px rgb(34 228 242 / 0.45)",
        "glow-magenta": "0 0 24px -4px rgb(255 61 129 / 0.45)",
        panel: "0 24px 60px -20px rgb(0 0 0 / 0.85)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "pulse-bar": {
          "0%, 100%": { transform: "scaleY(0.35)" },
          "50%": { transform: "scaleY(1)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.35s ease-out both",
        shimmer: "shimmer 1.8s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
