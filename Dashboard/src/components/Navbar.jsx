import { NavLink, useLocation } from "react-router-dom";

const T = {
  bg:     "#080c12",
  border: "#1e2a3a",
  muted:  "#4a6080",
  blue:   "#3b82f6",
  orange: "#f97316",
};

function Navbar() {
  const location  = useLocation();
  const currentSku = new URLSearchParams(location.search).get("sku") || "";
  const withSku   = (path) => currentSku ? `${path}?sku=${currentSku}` : path;

  const base = {
    textDecoration: "none", fontWeight: 700, fontSize: 13,
    padding: "7px 14px", borderRadius: 8,
    transition: "all 0.15s ease", letterSpacing: 0.2,
    fontFamily: "'IBM Plex Sans', sans-serif",
  };

  const blueStyle = ({ isActive }) => ({
    ...base,
    color:      isActive ? T.blue   : T.muted,
    background: isActive ? T.blue   + "15" : "transparent",
    border:    `1px solid ${isActive ? T.blue   + "33" : "transparent"}`,
  });

  const orangeStyle = ({ isActive }) => ({
    ...base,
    color:      isActive ? T.orange : T.muted,
    background: isActive ? T.orange + "15" : "transparent",
    border:    `1px solid ${isActive ? T.orange + "33" : "transparent"}`,
  });

  return (
    <nav style={{
      background: T.bg, borderBottom: `1px solid ${T.border}`,
      padding: "12px 28px", display: "flex", alignItems: "center", gap: 4,
    }}>
      {/* Brand */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        marginRight: 20, paddingRight: 20, borderRight: `1px solid ${T.border}`,
      }}>
        <div style={{
          width: 22, height: 22,
          background: `linear-gradient(135deg, ${T.blue}, #2dd4bf)`,
          borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12,
        }}>◈</div>
        <span style={{ fontSize: 10, letterSpacing: 3, color: T.muted, textTransform: "uppercase", fontWeight: 700, fontFamily: "'IBM Plex Sans', sans-serif" }}>
          SKU Intelligence
        </span>
      </div>

      <NavLink to={withSku("/")}                style={blueStyle}>Forecast</NavLink>
      <NavLink to={withSku("/inventory")}       style={blueStyle}>Inventory</NavLink>
      <NavLink to={withSku("/recommendation")}  style={blueStyle}>Recommendation</NavLink>
      <NavLink to="/admin"                      style={blueStyle}>Admin</NavLink>
    </nav>
  );
}

export default Navbar;