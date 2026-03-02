import { NavLink } from "react-router-dom";

function Navbar() {
  const linkStyle = ({ isActive }) => ({
    color: isActive ? "#388bfd" : "#c9d1d9",
    textDecoration: "none",
    fontWeight: 600,
    fontSize: 14,
    padding: "8px 14px",
    borderRadius: 8,
    background: isActive ? "#388bfd15" : "transparent",
    border: isActive ? "1px solid #388bfd33" : "1px solid transparent",
    transition: "all 0.2s ease"
  });

  return (
    <nav
      style={{
        background: "#0d1117",
        borderBottom: "1px solid #21262d",
        padding: "14px 28px",
        display: "flex",
        alignItems: "center",
        gap: 16
      }}
    >
      <div style={{
        fontSize: 11,
        letterSpacing: 3,
        color: "#6e7681",
        textTransform: "uppercase",
        marginRight: 20
      }}>
        ◈ SKU Intelligence
      </div>

      <NavLink to="/" style={linkStyle}>
        Forecast
      </NavLink>

      <NavLink to="/inventory" style={linkStyle}>
        Inventory
      </NavLink>

      <NavLink to="/recommendation" style={linkStyle}>
        Recommendation
      </NavLink>

      <NavLink to="/admin" style={linkStyle}>
        Admin
      </NavLink>

    </nav>
  );
}

export default Navbar;