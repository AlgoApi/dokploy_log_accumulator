import { useState } from "react";
import { api } from "../api.js";

export default function Login({ onOk }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/login", { method: "POST", body: { password } });
      onOk();
    } catch (err) {
      setError(err.message === "unauthorized" ? "Invalid password" : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="panel login-box form-grid" onSubmit={submit}>
        <h1>Log Accumulator</h1>
        <label>
          Password
          <input
            type="password"
            value={password}
            autoFocus
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? <div className="error">{error}</div> : null}
        <button type="submit" disabled={busy}>
          Sign in
        </button>
      </form>
    </div>
  );
}
