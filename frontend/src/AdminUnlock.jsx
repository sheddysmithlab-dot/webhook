import { useState } from "react";
import { getAdminToken, setAdminToken } from "./api.js";

export default function AdminUnlock({ error, onUnlock }) {
  const [token, setToken] = useState(getAdminToken());
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setAdminToken(token);
    try {
      await onUnlock();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="okbox" onSubmit={submit} style={{ marginBottom: 16 }}>
      <b>Admin token chahiye</b>
      <p className="hint" style={{ marginTop: 6 }}>
        VPS <span className="mono">.env</span> ki <span className="mono">ADMIN_TOKEN</span> yahan paste karo. Ek baar save hogi.
      </p>
      {error && <div className="err" style={{ marginTop: 10 }}>{error}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ADMIN_TOKEN"
          autoComplete="off"
          style={{ flex: 1, minWidth: 220 }}
        />
        <button className="btn" type="submit" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Unlock"}
        </button>
      </div>
    </form>
  );
}
