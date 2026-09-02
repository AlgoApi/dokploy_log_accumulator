import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "./api.js";

export default function Layout() {
  const navigate = useNavigate();

  async function logout() {
    await api("/api/logout", { method: "POST" });
    navigate("/login");
  }

  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">Log Accumulator</div>
        <nav>
          <NavLink to="/" end>
            Logs
          </NavLink>
          <NavLink to="/services">Services</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="topbar-right">
          <button className="secondary" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
