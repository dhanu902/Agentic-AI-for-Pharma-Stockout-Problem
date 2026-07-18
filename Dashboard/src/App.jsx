// src/App.jsx
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import Forecast       from "./pages/Forecast";
import Inventory      from "./pages/Inventory";
import Recommendation from "./pages/Recommendation";// import HorizonPage from "./pages/Horizon"; -- activate in future
import Insights       from "./pages/Insights";
import Admin          from "./pages/Admin";
import Navbar         from "./components/Navbar";

export default function App() {
  return (
    <Router>
      <Navbar />
      <Routes>
        <Route path="/"               element={<Forecast />} />
        <Route path="/inventory"      element={<Inventory />} />
        <Route path="/recommendation" element={<Recommendation />} />
        <Route path="/insights"       element={<Insights />} />
        <Route path="/admin"          element={<Admin />} />
      </Routes>
    </Router>
  );
}