// src/theme.js
// Single source of truth for the light theme.
// Every page imports T from here instead of defining its own token object.
//
// Usage:
//   import T from "../theme";   (from pages/)
//   import T from "./theme";    (from components/)

const T = {
  // ── Layout backgrounds ─────────────────────────────────────
  bg:       "#f5f7fa",   // page background  (was #080c12)
  surface:  "#ffffff",   // panels / inset surfaces  (was #0e1420)
  card:     "#ffffff",   // card backgrounds  (was #111827)
  panel:    "#f0f2f5",   // slightly tinted inset panel  (was #0e1420)

  // ── Borders ────────────────────────────────────────────────
  border:   "#d1d9e6",   // default border  (was #1e2a3a)
  borderHi: "#b0bdd0",   // hover / active border  (was #2a3a52)

  // ── Typography ─────────────────────────────────────────────
  text:     "#0f172a",   // primary text  (was #e2eaf6)
  muted:    "#64748b",   // secondary / hint text  (was #4a6080)
  subtle:   "#e2e8f4",   // very light tinted bg  (was #243044)

  // ── Accent colours (darkened for contrast on white) ────────
  blue:     "#2563eb",   // (was #3b82f6)
  green:    "#16a34a",   // (was #22c55e)
  amber:    "#d97706",   // (was #f59e0b)
  orange:   "#ea580c",   // (was #f97316)
  red:      "#dc2626",   // (was #ef4444)
  crimson:  "#991b1b",   // unchanged
  purple:   "#7c3aed",   // (was #a78bfa)
  teal:     "#0d9488",   // (was #2dd4bf)
  sky:      "#0284c7",   // (was #38bdf8)
  indigo:   "#4f46e5",   // (was #6366f1)
  rose:     "#e11d48",   // (was #fb7185)
};

export default T;
