import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

function dokployHref(base, path) {
  if (!base || !path) return null;
  return `${base.replace(/\/$/, "")}${path}`;
}

export default function Logs() {
  const [logs, setLogs] = useState([]);
  const [services, setServices] = useState([]);
  const [settings, setSettings] = useState(null);
  const [serviceId, setServiceId] = useState("");
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (serviceId) params.set("service_id", serviceId);
      if (level) params.set("level", level);
      if (q) params.set("q", q);
      const qs = params.toString();
      const data = await api(`/api/logs${qs ? `?${qs}` : ""}`);
      setLogs(data.logs);
      setUpdatedAt(new Date());
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }, [serviceId, level, q]);

  useEffect(() => {
    api("/api/services").then((d) => setServices(d.services)).catch(() => {});
    api("/api/settings").then(setSettings).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="form-grid">
      <div className="row">
        <label>
          Service
          <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
            <option value="">All</option>
            {services.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Level
          <select value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">All stored</option>
            <option value="error">error</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
            <option value="debug">debug</option>
          </select>
        </label>
        <label>
          Search
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="substring" />
        </label>
        <button onClick={load}>Refresh</button>
        {updatedAt ? (
          <span className="muted">Updated {updatedAt.toLocaleTimeString()}</span>
        ) : null}
      </div>
      {error ? <div className="error">{error}</div> : null}
      <div className="panel" style={{ padding: 0, overflow: "auto" }}>
        <table className="log-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Time</th>
              <th>Level</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {logs.length === 0 ? (
              <tr>
                <td colSpan="4" className="muted">
                  No log lines yet. Enable services in Settings and wait for the next poll.
                </td>
              </tr>
            ) : (
              logs.map((line) => {
                const href = dokployHref(settings?.dokploy_url, line.dokploy_path);
                return (
                  <tr
                    key={line.id}
                    className={href ? "clickable" : ""}
                    onClick={() => href && window.open(href, "_blank", "noopener")}
                  >
                    <td>
                      <span className="source">{line.source_label}</span>
                    </td>
                    <td className="muted">
                      {line.timestamp ? new Date(line.timestamp).toLocaleString() : "—"}
                    </td>
                    <td>
                      <span className={`badge ${line.level}`}>{line.level}</span>
                    </td>
                    <td className="message">{line.message}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
