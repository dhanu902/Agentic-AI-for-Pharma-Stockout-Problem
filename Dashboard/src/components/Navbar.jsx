// src/components/Navbar.jsx
// UI v2 — visual upgrade only; routing/logic unchanged.

import { NavLink, useLocation } from "react-router-dom";
import T from "../theme";

const FONT_UI = "'Inter', 'IBM Plex Sans', sans-serif";

/* Recommendation page accent — distinct from Inventory's orange.
   Swap for a T token (e.g. T.rose / T.red) if one exists in theme.js. */
const REC_ACCENT = "#E11D48";

export default function Navbar() {
  const location   = useLocation();
  const currentSku = new URLSearchParams(location.search).get("sku") || "";
  const withSku    = (path) => currentSku ? `${path}?sku=${currentSku}` : path;

  const base = {
    textDecoration: "none", fontWeight: 700, fontSize: 13,
    padding: "8px 16px", borderRadius: 999,
    transition: "all 0.18s cubic-bezier(0.22,1,0.36,1)", letterSpacing: 0.2,
    fontFamily: FONT_UI,
    display: "inline-flex", alignItems: "center", gap: 7,
  };

  /* Each nav item has its own page accent colour */
  const makeStyle = (activeColor) => ({ isActive }) => ({
    ...base,
    color:      isActive ? activeColor : T.muted,
    background: isActive ? activeColor + "14" : "transparent",
    border:    `1px solid ${isActive ? activeColor + "3D" : "transparent"}`,
    boxShadow:  isActive ? `inset 0 1px 0 ${activeColor}1A` : "none",
  });

  /* Active-dot rendered via NavLink children function */
  const linkContent = (label, color) => ({ isActive }) => (
    <>
      {isActive && <span style={{ width: 6, height: 6, borderRadius: "50%",
        background: color, boxShadow: `0 0 0 3px ${color}22`, flexShrink: 0 }} />}
      {label}
    </>
  );

  return (
    <nav style={{
      position: "sticky", top: 0, zIndex: 100,
      background: `linear-gradient(180deg, ${T.surface}F7, ${T.surface}E8)`,
      backdropFilter: "blur(12px)",
      WebkitBackdropFilter: "blur(12px)",
      borderBottom: `1px solid ${T.border}`,
      boxShadow: "0 1px 2px rgba(15,23,42,0.04), 0 8px 24px -20px rgba(15,23,42,0.25)",
      padding: "11px 28px", display: "flex", alignItems: "center", gap: 6,
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
        .nav-link-v2:hover { transform: translateY(-1px); }
      `}</style>

      {/* Brand */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        marginRight: 20, paddingRight: 20, borderRight: `1px solid ${T.border}`,
      }}>
        <div style={{
          width: 28, height: 28,
          background: `linear-gradient(135deg, ${T.blue}, ${T.teal})`,
          borderRadius: 8, display: "flex", alignItems: "center",
          justifyContent: "center", fontSize: 13, color: "#fff",
          boxShadow: `0 6px 14px -6px ${T.blue}99`,
        }}>◈</div>
        <div>
          <div style={{
            fontSize: 10, letterSpacing: 3, color: T.text,
            textTransform: "uppercase", fontWeight: 900,
            fontFamily: FONT_UI, lineHeight: 1.2,
          }}>
            SKU Intelligence
          </div>
          <div style={{ fontSize: 8, letterSpacing: 1.6, color: T.muted,
            textTransform: "uppercase", fontWeight: 700, fontFamily: FONT_UI }}>
            Pharma Supply Suite
          </div>
        </div>
      </div>

      {/* ── Nav links — Insights first, then the pipeline order ── */}
      <NavLink className="nav-link-v2" to="/insights"             style={makeStyle(T.purple)}>{linkContent("Insights", T.purple)}</NavLink>
      <NavLink className="nav-link-v2" to={withSku("/")}          style={makeStyle(T.blue)}>{linkContent("Forecast", T.blue)}</NavLink>
      <NavLink className="nav-link-v2" to={withSku("/inventory")} style={makeStyle(T.orange)}>{linkContent("Inventory", T.orange)}</NavLink>
      <NavLink className="nav-link-v2" to={withSku("/recommendation")} style={makeStyle(REC_ACCENT)}>{linkContent("Recommend", REC_ACCENT)}</NavLink>
      <NavLink className="nav-link-v2" to="/admin"                style={makeStyle(T.teal)}>{linkContent("Admin", T.teal)}</NavLink>
    </nav>
  );
}