import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import Forecast from "./pages/Forecast";
import Inventory from "./pages/Inventory";
import Recommendation from "./pages/Recommendation";
import Navbar from "./components/Navbar";
import Admin from "./pages/Admin";

function App() {
  return (
    <Router>
      <Navbar />
      <Routes>
        <Route path="/" element={<Forecast />} />
        <Route path="/inventory" element={<Inventory />} />
        <Route path="/recommendation" element={<Recommendation />} />
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </Router>
  );
}

export default App;