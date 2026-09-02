import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

function dokployHref(base, path) {
  if (!base || !path) return null;
  return `${base.replace(/\/$/, "")}${path}`;
}

export default function Services() {
  const [services, setServices] = useState([]);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [svc, cfg] = await Promise.all([api("/api/services"), api("/api/settings")]);
      setServices(svc.services);
      setSettings(cfg);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  async function sync() {
    setBusy(true);
    try {
      await api("/api/services/sync", { method: "POST" });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form-grid">
      <div className="row">
        <button onClick={sync} disabled={busy}>
          Sync from Dokploy
        </button>
        <button className="secondary" onClick={load}>
          Refresh
        </button>
      </div>
      {error ? <div className="error">{error}</div> : null}
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Enabled</th>
              <th>Last fetch</th>
              <th>Lines</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {services.map((s) => {
              const href = dokployHref(settings?.dokploy_url, s.dokploy_path);
              return (
                <tr
                  key={s.id}
                  className={href ? "clickable" : ""}
                  onClick={() => href && window.open(href, "_blank", "noopener")}
                >
                  <td className="source">{s.name}</td>
                  <td>{s.dokploy_type}</td>
                  <td>{s.enabled ? "yes" : "no"}</td>
                  <td className="muted">
                    {s.last_fetch_at ? new Date(s.last_fetch_at).toLocaleString() : "—"}
                  </td>
                  <td>{s.log_count}</td>
                  <td className="error">{s.last_error || ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
