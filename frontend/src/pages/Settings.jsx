import { useEffect, useState } from "react";
import { api } from "../api.js";

function lines(value) {
  return (value || []).join("\n");
}

function fromLines(text) {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

const emptyForm = {
  dokploy_url: "",
  dokploy_api_key: "",
  project_id: "",
  poll_interval_sec: 60,
  log_since: "2m",
  log_tail: 300,
  level_filter: "warning_error",
  exclude_patterns: "",
  exclude_regex: "",
  keywords: "",
  keyword_mode: "any",
  max_lines_per_service: 500,
  self_application_id: "",
};

export default function Settings() {
  const [form, setForm] = useState(emptyForm);
  const [hasKey, setHasKey] = useState(false);
  const [projects, setProjects] = useState([]);
  const [services, setServices] = useState([]);
  const [enabled, setEnabled] = useState({});
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  function patch(fields) {
    setForm((prev) => ({ ...prev, ...fields }));
  }

  async function load() {
    const cfg = await api("/api/settings");
    setHasKey(cfg.has_api_key);
    setForm({
      dokploy_url: cfg.dokploy_url,
      dokploy_api_key: "",
      project_id: cfg.project_id,
      poll_interval_sec: cfg.poll_interval_sec,
      log_since: cfg.log_since,
      log_tail: cfg.log_tail,
      level_filter: cfg.level_filter,
      exclude_patterns: lines(cfg.exclude_patterns),
      exclude_regex: lines(cfg.exclude_regex),
      keywords: lines(cfg.keywords),
      keyword_mode: cfg.keyword_mode,
      max_lines_per_service: cfg.max_lines_per_service,
      self_application_id: cfg.self_application_id,
    });
    const svc = await api("/api/services");
    setServices(svc.services);
    setEnabled(Object.fromEntries(svc.services.map((s) => [s.id, s.enabled])));
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function saveSettings() {
    setBusy(true);
    setError("");
    setOk("");
    const body = {
      dokploy_url: form.dokploy_url,
      project_id: form.project_id,
      poll_interval_sec: Number(form.poll_interval_sec),
      log_since: form.log_since,
      log_tail: Number(form.log_tail),
      level_filter: form.level_filter,
      exclude_patterns: fromLines(form.exclude_patterns),
      exclude_regex: fromLines(form.exclude_regex),
      keywords: fromLines(form.keywords),
      keyword_mode: form.keyword_mode,
      max_lines_per_service: Number(form.max_lines_per_service),
      self_application_id: form.self_application_id,
    };
    if (form.dokploy_api_key.trim()) {
      body.dokploy_api_key = form.dokploy_api_key.trim();
    }
    try {
      await api("/api/settings", { method: "PUT", body });
      setForm((prev) => ({ ...prev, dokploy_api_key: "" }));
      if (form.dokploy_api_key.trim() || hasKey) setHasKey(true);
      setOk("Settings saved");
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function loadProjects() {
    setError("");
    try {
      await saveSettings();
      const data = await api("/api/dokploy/projects");
      setProjects(data.projects);
      setOk("Projects loaded");
    } catch (err) {
      setError(err.message);
    }
  }

  async function syncServices() {
    setError("");
    try {
      await saveSettings();
      await api("/api/services/sync", { method: "POST" });
      const svc = await api("/api/services");
      setServices(svc.services);
      setEnabled((prev) => {
        const next = {};
        for (const s of svc.services) {
          next[s.id] = s.id in prev ? prev[s.id] : s.enabled;
        }
        return next;
      });
      setOk("Service list updated");
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveEnabled() {
    setBusy(true);
    setError("");
    try {
      await api("/api/services", {
        method: "PATCH",
        body: {
          services: Object.entries(enabled).map(([id, value]) => ({
            id: Number(id),
            enabled: Boolean(value),
          })),
        },
      });
      setOk("Tracked services updated");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function pollNow() {
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/poll/now", { method: "POST" });
      setOk(`Poll finished: ${result.polled || 0} ok, ${result.failed || 0} failed`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form-grid">
      <div className="panel form-grid">
        <h2>Dokploy</h2>
        <label>
          URL
          <input
            value={form.dokploy_url}
            onChange={(e) => patch({ dokploy_url: e.target.value })}
            placeholder="https://dokploy.example.com"
          />
        </label>
        <label>
          API key {hasKey ? "(saved, leave empty to keep)" : ""}
          <input
            type="password"
            value={form.dokploy_api_key}
            onChange={(e) => patch({ dokploy_api_key: e.target.value })}
            placeholder={hasKey ? "••••••••" : ""}
          />
        </label>
        <div className="row">
          <button onClick={loadProjects} disabled={busy}>
            Load projects
          </button>
        </div>
        <label>
          Project
          <select
            value={form.project_id}
            onChange={(e) => patch({ project_id: e.target.value })}
          >
            <option value="">Select…</option>
            {form.project_id && !projects.some((p) => p.projectId === form.project_id) ? (
              <option value={form.project_id}>{form.project_id}</option>
            ) : null}
            {projects.map((p) => (
              <option key={p.projectId} value={p.projectId}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Skip this application ID (the accumulator itself)
          <input
            value={form.self_application_id}
            onChange={(e) => patch({ self_application_id: e.target.value })}
          />
        </label>
      </div>

      <div className="panel form-grid">
        <h2>Polling</h2>
        <div className="row">
          <label>
            Interval (sec)
            <input
              type="number"
              value={form.poll_interval_sec}
              onChange={(e) => patch({ poll_interval_sec: e.target.value })}
            />
          </label>
          <label>
            since
            <input
              value={form.log_since}
              onChange={(e) => patch({ log_since: e.target.value })}
            />
          </label>
          <label>
            tail
            <input
              type="number"
              value={form.log_tail}
              onChange={(e) => patch({ log_tail: e.target.value })}
            />
          </label>
          <label>
            Buffer / service
            <input
              type="number"
              value={form.max_lines_per_service}
              onChange={(e) => patch({ max_lines_per_service: e.target.value })}
            />
          </label>
        </div>
      </div>

      <div className="panel form-grid">
        <h2>Filters</h2>
        <label>
          Level filter
          <select
            value={form.level_filter}
            onChange={(e) => patch({ level_filter: e.target.value })}
          >
            <option value="off">off (keep all levels)</option>
            <option value="warning_error">warning + error</option>
            <option value="error_only">error only</option>
          </select>
        </label>
        <label>
          Exclude substrings (one per line)
          <textarea
            value={form.exclude_patterns}
            onChange={(e) => patch({ exclude_patterns: e.target.value })}
          />
        </label>
        <label>
          Exclude regex (one per line)
          <textarea
            value={form.exclude_regex}
            onChange={(e) => patch({ exclude_regex: e.target.value })}
          />
        </label>
        <label>
          Keywords (one per line; empty = no keyword filter)
          <textarea value={form.keywords} onChange={(e) => patch({ keywords: e.target.value })} />
        </label>
        <label>
          Keyword mode
          <select
            value={form.keyword_mode}
            onChange={(e) => patch({ keyword_mode: e.target.value })}
          >
            <option value="any">any</option>
            <option value="all">all</option>
          </select>
        </label>
        <div className="row">
          <button onClick={saveSettings} disabled={busy}>
            Save settings
          </button>
          <button className="secondary" onClick={pollNow} disabled={busy}>
            Poll now
          </button>
        </div>
      </div>

      <div className="panel form-grid">
        <h2>Tracked services</h2>
        <div className="row">
          <button onClick={syncServices} disabled={busy}>
            Sync list from project
          </button>
          <button className="secondary" onClick={saveEnabled} disabled={busy}>
            Save enabled services
          </button>
        </div>
        <div className="service-list">
          {services.length === 0 ? (
            <div className="muted">Sync a project to see applications and compose stacks.</div>
          ) : (
            services.map((s) => (
              <label key={s.id} className="service-item" style={{ flexDirection: "row" }}>
                <input
                  type="checkbox"
                  checked={Boolean(enabled[s.id])}
                  onChange={(e) =>
                    setEnabled((prev) => ({ ...prev, [s.id]: e.target.checked }))
                  }
                />
                <span>
                  {s.name} <span className="muted">({s.dokploy_type})</span>
                </span>
              </label>
            ))
          )}
        </div>
      </div>

      {error ? <div className="error">{error}</div> : null}
      {ok ? <div className="muted">{ok}</div> : null}
    </div>
  );
}
