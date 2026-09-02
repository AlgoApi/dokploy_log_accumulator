import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api.js";
import Layout from "./Layout.jsx";
import Login from "./pages/Login.jsx";
import Logs from "./pages/Logs.jsx";
import Services from "./pages/Services.jsx";
import Settings from "./pages/Settings.jsx";

function Guard({ children }) {
  const [state, setState] = useState("loading");
  const location = useLocation();

  useEffect(() => {
    let cancelled = false;
    api("/api/me")
      .then(() => {
        if (!cancelled) setState("ok");
      })
      .catch((err) => {
        if (!cancelled) setState(err.status === 401 ? "anon" : "ok");
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (state === "loading") return <div className="content muted">Loading…</div>;
  if (state === "anon") return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  const navigate = useNavigate();
  return (
    <Routes>
      <Route path="/login" element={<Login onOk={() => navigate("/")} />} />
      <Route
        element={
          <Guard>
            <Layout />
          </Guard>
        }
      >
        <Route path="/" element={<Logs />} />
        <Route path="/services" element={<Services />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
