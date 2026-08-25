import { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api, fmtTime, fmtTimeLong, isAdminAuthError } from "../api.js";

const EVENT_LABELS = [
  ["account_check", "Account Check"],
  ["account_create", "Account Create"],
  ["otp_request", "OTP Request"],
  ["otp_verify", "OTP Verify"],
  ["listing_push", "Listing Push"],
  ["media_push", "Media Push"],
  ["profile_update", "Profile Update"],
];

const TABS = [
  { id: "log", label: "Request Log" },
  { id: "accounts", label: "Account Creation" },
  { id: "callbacks", label: "Callbacks" },
  { id: "docs", label: "API Docs" },
  { id: "brand", label: "WhatsApp Brand" },
  { id: "settings", label: "Settings" },
];

function statusTone(status) {
  const s = String(status || "").toUpperCase();
  if (s === "APPROVED") return "ok";
  if (s.includes("PENDING") || s === "AVAILABLE_WITHOUT_REVIEW") return "warn";
  if (s.includes("DECLIN") || s.includes("REJECT") || s === "EXPIRED") return "bad";
  return "plain";
}

const ACCOUNT_EVENTS = new Set(["ACCOUNT_CHECK", "ACCOUNT_CREATE", "OTP_REQUEST", "OTP_VERIFY"]);

const API_CONTRACT = {
  baseUrl: "https://api.infradealer.com/api/v1/webhook",
  authentication: {
    headers: [
      "X-InfraDealer-Key",
      "X-InfraDealer-Timestamp",
      "X-InfraDealer-Signature",
      "X-InfraDealer-Request-ID",
    ],
    signature: 'HMAC-SHA256(secret, timestamp + "." + request_id + "." + raw_request_body)',
  },
  endpoints: [
    { method: "POST", path: "/account/check", description: "Check if phone has InfraDealer account" },
    { method: "POST", path: "/listing/push", description: "Push listing for matched account (pending review)" },
    { method: "POST", path: "/account/create", description: "Start account creation + OTP" },
    { method: "POST", path: "/otp/request", description: "Resend OTP" },
    { method: "POST", path: "/otp/verify", description: "Verify OTP and create account" },
    { method: "POST", path: "/test", description: "Test authentication" },
    { method: "GET", path: "/status", description: "Integration status" },
  ],
  errorCodes: [
    "ACCOUNT_NOT_FOUND", "ACCOUNT_BLOCKED", "ACCOUNT_EXISTS", "INVALID_PHONE",
    "INVALID_API_KEY", "INVALID_SIGNATURE", "INVALID_PAYLOAD", "DUPLICATE_REQUEST",
    "LISTING_VALIDATION_FAILED", "OTP_REQUEST_FAILED", "OTP_INVALID", "OTP_EXPIRED",
    "OTP_ATTEMPTS_EXCEEDED", "ACCOUNT_CREATION_FAILED", "INTERNAL_ERROR",
  ],
};

function parseCredentials(text) {
  const raw = (text || "").trim();
  const out = { base_url: "", api_key: "", api_secret: "", integration_id: "" };
  if (!raw) return out;
  try {
    const j = JSON.parse(raw);
    out.base_url = j.baseUrl || j.base_url || "";
    out.api_key = j.apiKey || j.api_key || j.key || "";
    out.api_secret = j.secret || j.api_secret || j.apiSecret || "";
    out.integration_id = j.integrationId || j.integration_id || j.id || "";
  } catch {
    /* line paste */
  }
  const id = raw.match(/whk_[a-f0-9]+/i);
  const key = raw.match(/idk_[a-f0-9]+/i);
  const secret = raw.match(/ids_[a-f0-9]+/i);
  const url = raw.match(/https?:\/\/[^\s"]+/i);
  if (id) out.integration_id = id[0];
  if (key) out.api_key = key[0];
  if (secret) out.api_secret = secret[0];
  if (url && !out.base_url) out.base_url = url[0].replace(/\/+$/, "");
  if (!out.base_url) out.base_url = API_CONTRACT.baseUrl;
  return out;
}

function applyPaste(value, setPaste, setForm) {
  setPaste(value);
  const parsed = parseCredentials(value);
  if (parsed.api_key || parsed.api_secret || parsed.integration_id || parsed.base_url) {
    setForm((f) => ({
      ...f,
      base_url: parsed.base_url || f.base_url || API_CONTRACT.baseUrl,
      api_key: parsed.api_key || f.api_key,
      api_secret: parsed.api_secret || f.api_secret,
      integration_id: parsed.integration_id || f.integration_id,
    }));
  }
}

function EyeIcon({ open }) {
  if (open) {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C7 20 2.73 16.11 1 12c.64-1.53 1.6-2.92 2.8-4.06M9.9 4.24A10.94 10.94 0 0 1 12 4c5 0 9.27 3.89 11 8a11.5 11.5 0 0 1-2.16 3.19" />
        <path d="M1 1l22 22" />
        <path d="M14.12 14.12A3 3 0 0 1 9.88 9.88" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function SecretField({ label, value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="field">
      <label>{label}</label>
      <div className="infra-secret">
        <input
          type={show ? "text" : "password"}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          autoComplete="off"
        />
        <button
          type="button"
          className="infra-eye"
          aria-label={show ? `Hide ${label}` : `Show ${label}`}
          onClick={() => setShow((v) => !v)}
        >
          <EyeIcon open={show} />
        </button>
      </div>
    </div>
  );
}

function LedgerTable({ rows, empty, onDetail }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Request ID</th><th>Event</th><th>Phone</th>
          <th>User ID</th><th>Status</th><th>Attempts</th><th>HTTP</th><th></th>
        </tr>
      </thead>
      <tbody>
        {!rows.length && <tr><td colSpan={9} className="tbl-empty">{empty}</td></tr>}
        {rows.map((row) => (
          <tr key={row.id}>
            <td>{fmtTime(row.time)}</td>
            <td className="mono">{row.request_id}</td>
            <td>{row.event}</td>
            <td>{row.phone}</td>
            <td>{row.user_id}</td>
            <td>{row.status}{row.business_code ? ` / ${row.business_code}` : ""}</td>
            <td>{row.attempts ?? 1}</td>
            <td>{row.http_status || "—"}</td>
            <td><button type="button" className="btn small" onClick={() => onDetail(row.request_id)}>Detail</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function InfraDealerIntegration() {
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState("docs");
  const [ledger, setLedger] = useState([]);
  const [errors, setErrors] = useState([]);
  const [callbacks, setCallbacks] = useState([]);
  const [detail, setDetail] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [secretOnce, setSecretOnce] = useState("");
  const [form, setForm] = useState({
    base_url: API_CONTRACT.baseUrl,
    api_key: "",
    api_secret: "",
    api_version: "v1",
    mode: "LIVE",
    integration_id: "",
  });
  const [paste, setPaste] = useState("");
  const [brand, setBrand] = useState(null);
  const [brandName, setBrandName] = useState("Infradealer");
  const [brandMsg, setBrandMsg] = useState("");
  const [brandAck, setBrandAck] = useState(false);
  const [brandPin, setBrandPin] = useState("");
  const [filters, setFilters] = useState({
    phone: "", request_id: "", user_id: "", event: "",
    failed_only: false, pending_only: false, account_only: false, listing_only: false,
  });

  const loadBrand = useCallback(async () => {
    const data = await api.displayNameStatus();
    setBrand(data);
    if (data?.new_display_name) setBrandName(data.new_display_name);
    else if (data?.last_webhook?.requested_name) setBrandName(data.last_webhook.requested_name);
    else if (!data?.verified_name) setBrandName("Infradealer");
    return data;
  }, []);

  const refresh = useCallback(async () => {
    const [c, l, e, cb] = await Promise.all([
      api.infraConfig(),
      api.infraLedger(filters),
      api.infraErrors(),
      api.infraCallbacks().catch(() => []),
    ]);
    setCfg(c);
    setLedger(l);
    setErrors(e);
    setCallbacks(cb);
    setForm((f) => ({
      ...f,
      base_url: c.base_url || API_CONTRACT.baseUrl,
      api_version: c.api_version || "v1",
      mode: c.mode || "LIVE",
      integration_id: c.integration_id || "",
    }));
  }, [filters]);

  useEffect(() => {
    refresh().catch((e) => setErr(e.message));
  }, [refresh]);

  useEffect(() => {
    if (tab !== "brand") return;
    setBusy("brand");
    setBrandMsg("");
    loadBrand()
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(""));
  }, [tab, loadBrand]);

  async function refreshBrand() {
    setBusy("brand");
    setErr("");
    setBrandMsg("");
    try {
      await loadBrand();
      setBrandMsg("Status Meta se refresh ho gaya.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function submitBrand() {
    const name = brandName.trim();
    if (!name) {
      setErr("Display name likho (e.g. Infradealer).");
      return;
    }
    if (!brandAck) {
      setErr("Pehle Meta branding rules confirm karo.");
      return;
    }
    if (!window.confirm(`"${name}" Meta review ke liye submit karein? Instant change nahi hota.`)) return;
    setBusy("brand-submit");
    setErr("");
    setBrandMsg("");
    try {
      const data = await api.displayNameSubmit(name);
      setBrand(data);
      setBrandMsg(data.message || "Submit ho gaya.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function applyBrand() {
    const pin = String(brandPin || "").replace(/\D/g, "");
    if (pin.length !== 6) {
      setErr("WhatsApp two-step verification PIN (6 digits) chahiye.");
      return;
    }
    if (!window.confirm("Approve hone ke baad phone re-register hoga. Display name customers pe apply hoga. Continue?")) return;
    setBusy("brand-register");
    setErr("");
    setBrandMsg("");
    try {
      const data = await api.displayNameRegister(pin);
      setBrand(data);
      setBrandPin("");
      setBrandMsg(data.message || "Display name apply ho gaya.");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function saveSettings() {
    setBusy("save");
    setErr("");
    try {
      const body = {
        base_url: form.base_url || API_CONTRACT.baseUrl,
        api_version: form.api_version,
        mode: form.mode,
      };
      if (form.integration_id) body.integration_id = form.integration_id;
      if (form.api_key) body.api_key = form.api_key;
      if (form.api_secret) body.api_secret = form.api_secret;
      const c = await api.infraSave(body);
      setCfg(c);
      setForm((f) => ({ ...f, api_key: "", api_secret: "" }));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function testConnection() {
    setBusy("test");
    setTestResult(null);
    try {
      const r = await api.infraTest();
      setTestResult(r);
      await refresh();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function toggleEvent(key, value) {
    if (!value && !window.confirm(`${key} disable karna hai? Critical events band ho sakte hain.`)) return;
    const flags = { ...cfg.event_flags, [key]: value };
    const c = await api.infraEvents(flags);
    setCfg(c);
  }

  async function openDetail(requestId) {
    const d = await api.infraLedgerDetail(requestId);
    setDetail(d);
  }

  if (!cfg) {
    if (err && isAdminAuthError(err)) return <Navigate to="/login" replace />;
    return err ? <div className="err">{err}</div> : <p className="lede">InfraDealer Integration load ho raha hai…</p>;
  }

  const stats = cfg.stats || {};
  const health = cfg.health || {};
  const shortKey = cfg.api_key_short || cfg.api_key_masked || "—";
  const connected = !!cfg.connected;
  const accountRows = ledger.filter((row) => ACCOUNT_EVENTS.has(row.event));
  const metricCards = [
    ["requests", "Requests today", stats.requests_today],
    ["ok", "Successful", stats.successful],
    ["bad", "Failed", stats.failed],
    ["plain", "Listing pushes", stats.listing_pushes],
    ["plain", "Account matches", stats.account_matches],
    ["warn", "OTP pending", stats.otp_pending],
    ["ok", "Accounts created", stats.new_accounts],
    ["bad", "Callback errors", stats.callback_errors],
  ];

  return (
    <div className="wh-page">
      <div className="wh-head">
        <div>
          <div className="wh-crumb">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            Integrations
          </div>
          <h1>Webhook API</h1>
          <p>WhatsApp automation ↔ InfraDealer secure bridge</p>
        </div>
        <div className="wh-head-actions">
          <span className={`wh-pill${connected ? " on" : ""}`}>
            <i />
            {connected ? "CONNECTED" : (cfg.connection_status || "DISCONNECTED")}
          </span>
          <button type="button" className="btn dash-primary" disabled={busy === "save"} onClick={saveSettings}>
            Save Settings
          </button>
        </div>
      </div>

      {err && <div className="err">{err}</div>}
      {secretOnce && (
        <div className="okbox">
          <b>API Secret (ek baar dikhega — copy kar lo):</b>
          <div className="mono" style={{ wordBreak: "break-all", marginTop: 8 }}>{secretOnce}</div>
        </div>
      )}
      {testResult && (
        <div className={testResult.ok ? "okbox" : "err"}>
          <b>{testResult.message}</b>
          <div>Authentication: {testResult.authentication ? "✓" : "✗"} · API: {testResult.api ? "✓" : "✗"} · Latency: {testResult.latency_ms} ms</div>
        </div>
      )}

      <div className="wh-stats">
        {metricCards.map(([tone, label, n]) => (
          <div className={`wh-stat ${tone}`} key={label}>
            <span>{label}</span>
            <b className="mono">{n ?? 0}</b>
          </div>
        ))}
      </div>
      <p className="wh-last">Last request: {fmtTimeLong(cfg.last_request_at || cfg.last_sync_at)}</p>

      <div className="wh-split">
        <section className="wh-card">
          <h2>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />
            </svg>
            Integration
          </h2>
          <select
            className="wh-select"
            value={cfg.integration_id || ""}
            onChange={() => {}}
          >
            <option value={cfg.integration_id || ""}>
              WhatsApp Webhook — {shortKey}
            </option>
          </select>
          <dl className="wh-meta">
            <div><dt>API Key</dt><dd className="mono">{shortKey}</dd></div>
            <div><dt>Status</dt><dd><span className="wh-active">Active</span></dd></div>
            <div><dt>Created</dt><dd>{fmtTimeLong(cfg.created_at)}</dd></div>
            <div><dt>Last Used</dt><dd>{fmtTimeLong(cfg.last_success_at || cfg.last_sync_at)}</dd></div>
          </dl>
          <div className="wh-row-btns">
            <button
              type="button"
              className="btn small"
              onClick={async () => {
                const r = await api.infraRegenSecret();
                setSecretOnce(r.api_secret_once || "");
                setCfg(r);
                setTab("settings");
              }}
            >
              Regenerate
            </button>
            <button type="button" className="btn small" onClick={() => api.infraDisconnect().then(refresh)}>
              Deactivate
            </button>
          </div>
          <button
            type="button"
            className="btn wh-test"
            disabled={busy === "test"}
            onClick={testConnection}
          >
            ▶ Test Connection
          </button>
          <div className="wh-foot">
            Connection: <b>{testResult ? (testResult.ok ? "SUCCESS" : "FAILED") : (connected ? "SUCCESS" : "—")}</b>
            {" · "}Auth: <b>{health.authentication_valid || connected ? "SUCCESS" : "—"}</b>
          </div>
        </section>

        <section className="wh-window">
          <div className="wh-tabs" role="tablist" aria-label="Webhook modules">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                className={`wh-tab${tab === item.id ? " on" : ""}`}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="wh-pane">
            {tab === "log" && (
              <>
                <div className="tbl-head">
                  <h3>Request Log</h3>
                  <div className="tools">
                    <input placeholder="Phone" value={filters.phone} onChange={(e) => setFilters({ ...filters, phone: e.target.value })} />
                    <input placeholder="Request ID" value={filters.request_id} onChange={(e) => setFilters({ ...filters, request_id: e.target.value })} />
                    <select value={filters.event} onChange={(e) => setFilters({ ...filters, event: e.target.value })}>
                      <option value="">All events</option>
                      {["ACCOUNT_CHECK", "ACCOUNT_CREATE", "OTP_REQUEST", "OTP_VERIFY", "LISTING_PUSH", "MEDIA_PUSH", "CONNECTION_TEST"].map((ev) => (
                        <option key={ev} value={ev}>{ev}</option>
                      ))}
                    </select>
                    <button type="button" className="btn small" onClick={() => refresh().catch((e) => setErr(e.message))}>Filter</button>
                  </div>
                </div>
                <LedgerTable rows={ledger} empty="No records yet." onDetail={openDetail} />
              </>
            )}

            {tab === "accounts" && (
              <>
                <div className="tbl-head">
                  <h3>Account Creation</h3>
                </div>
                <LedgerTable rows={accountRows} empty="No account events yet." onDetail={openDetail} />
              </>
            )}

            {tab === "callbacks" && (
              <>
                <div className="field" style={{ marginBottom: 16 }}>
                  <label>Callback URL</label>
                  <div className="infra-secret">
                    <input readOnly value={cfg.callback_url || "—"} className="mono" />
                    <button
                      type="button"
                      className="infra-eye"
                      onClick={() => navigator.clipboard.writeText(cfg.callback_url || "")}
                      aria-label="Copy callback URL"
                    >
                      Copy
                    </button>
                  </div>
                  <p className="hint">InfraDealer Admin → Webhook → Callback card me ye URL save karo.</p>
                </div>
                <table>
                  <thead>
                    <tr><th>Time</th><th>Callback ID</th><th>Event</th><th>Request ID</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    {!callbacks.length && <tr><td colSpan={5} className="tbl-empty">No callbacks yet.</td></tr>}
                    {callbacks.map((row) => (
                      <tr key={row.id}>
                        <td>{fmtTime(row.time)}</td>
                        <td className="mono">{row.callback_id}</td>
                        <td>{row.event}</td>
                        <td className="mono">{row.request_id}</td>
                        <td>{row.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!!errors.length && (
                  <>
                    <h3 className="h3" style={{ marginTop: 20 }}>Errors</h3>
                    <table>
                      <thead>
                        <tr><th>Time</th><th>Request ID</th><th>Event</th><th>Code</th><th></th></tr>
                      </thead>
                      <tbody>
                        {errors.map((row) => (
                          <tr key={row.request_id + row.time}>
                            <td>{fmtTime(row.time)}</td>
                            <td className="mono">{row.request_id}</td>
                            <td>{row.event}</td>
                            <td>{row.error_code}</td>
                            <td>
                              {row.next_action === "manual_retry" && (
                                <button type="button" className="btn small" onClick={() => api.infraRetry(row.request_id).then(refresh)}>Retry</button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </>
            )}

            {tab === "docs" && (
              <div className="wh-docs">
                <div className="tbl-head" style={{ padding: 0, border: 0, background: "transparent" }}>
                  <h3>API Docs</h3>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => navigator.clipboard.writeText(JSON.stringify(API_CONTRACT, null, 2))}
                  >
                    Copy JSON
                  </button>
                </div>
                <p className="wh-label">Base URL</p>
                <div className="wh-code">{cfg.base_url || API_CONTRACT.baseUrl}</div>
                <p className="wh-label">Authentication</p>
                <p className="lede" style={{ marginBottom: 8 }}>{API_CONTRACT.authentication.signature}</p>
                <ul className="wh-headers">
                  {API_CONTRACT.authentication.headers.map((h) => <li key={h} className="mono">{h}</li>)}
                </ul>
                <p className="wh-label">Endpoints</p>
                <ul className="wh-eps">
                  {API_CONTRACT.endpoints.map((ep) => (
                    <li key={ep.path}>
                      <span className={`wh-method ${ep.method.toLowerCase()}`}>{ep.method}</span>
                      <code>{ep.path}</code>
                      <span>{ep.description}</span>
                    </li>
                  ))}
                </ul>
                <p className="wh-label">Error codes</p>
                <p className="lede" style={{ marginBottom: 0 }}>{API_CONTRACT.errorCodes.join(" · ")}</p>
              </div>
            )}

            {tab === "brand" && (
              <div className="wh-brand">
                <div className="tbl-head">
                  <h3>WhatsApp display name</h3>
                  <button
                    type="button"
                    className="btn small"
                    disabled={busy === "brand" || busy === "brand-submit" || busy === "brand-register"}
                    onClick={refreshBrand}
                  >
                    {busy === "brand" ? "Refreshing…" : "Refresh status"}
                  </button>
                </div>
                <p className="lede">
                  Full flow: Submit → Meta review → PIN se Apply (re-register) → unsaved contacts ko{" "}
                  <b>Infradealer</b> dikhega. Green verify badge alag process hai.
                </p>

                {brand?.steps?.length > 0 && (
                  <ol className="wh-brand-steps">
                    {brand.steps.map((s) => (
                      <li key={s.id} className={`wh-brand-step ${s.state}`}>
                        <span className="wh-brand-step-mark" aria-hidden />
                        <span>{s.label}</span>
                      </li>
                    ))}
                  </ol>
                )}

                {!brand && busy === "brand" && <p className="lede">Meta status load ho raha hai…</p>}

                {brand && !brand.configured && (
                  <div className="err">
                    {brand.message || "Meta credentials missing."}
                    {" "}
                    <Link to="/meta">/meta settings</Link> pe Phone Number ID + System User Token save karo.
                    Phir Webhook fields mein <code>phone_number_name_update</code> on karke Subscribe karo.
                  </div>
                )}

                {brand && brand.configured && (
                  <dl className="wh-brand-meta">
                    <div>
                      <dt>Phone</dt>
                      <dd>{brand.display_phone_number || "—"}</dd>
                    </div>
                    <div>
                      <dt>Current name</dt>
                      <dd>{brand.verified_name || "—"}</dd>
                    </div>
                    <div>
                      <dt>Live status</dt>
                      <dd>
                        <span className={`wh-brand-badge ${statusTone(brand.name_status)}`}>
                          {brand.name_status || "—"}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>Pending name</dt>
                      <dd>{brand.new_display_name || "—"}</dd>
                    </div>
                    <div>
                      <dt>Pending status</dt>
                      <dd>
                        <span className={`wh-brand-badge ${statusTone(brand.new_name_status)}`}>
                          {brand.new_name_status || "NONE"}
                        </span>
                      </dd>
                    </div>
                    {brand.last_webhook?.decision ? (
                      <div>
                        <dt>Last webhook</dt>
                        <dd>
                          <span className={`wh-brand-badge ${statusTone(brand.last_webhook.decision)}`}>
                            {brand.last_webhook.decision}
                          </span>
                          {brand.last_webhook.requested_name ? ` · ${brand.last_webhook.requested_name}` : ""}
                        </dd>
                      </div>
                    ) : null}
                    {brand.last_webhook?.rejection_reason ? (
                      <div>
                        <dt>Rejection</dt>
                        <dd>{brand.last_webhook.rejection_reason}</dd>
                      </div>
                    ) : null}
                    {brand.quality_rating ? (
                      <div>
                        <dt>Quality</dt>
                        <dd>{brand.quality_rating}</dd>
                      </div>
                    ) : null}
                  </dl>
                )}

                {brandMsg && <div className="okbox">{brandMsg}</div>}

                {brand?.configured && (
                  <>
                    <h3 className="h3" style={{ marginTop: 16 }}>1. Submit for Meta review</h3>
                    <div className="field full">
                      <label>New display name</label>
                      <input
                        value={brandName}
                        onChange={(e) => setBrandName(e.target.value)}
                        placeholder="Infradealer"
                        maxLength={512}
                        disabled={!brand.can_submit || busy === "brand-submit"}
                      />
                    </div>
                    <label className="wh-brand-ack">
                      <input
                        type="checkbox"
                        checked={brandAck}
                        onChange={(e) => setBrandAck(e.target.checked)}
                        disabled={!brand.can_submit}
                      />
                      <span>
                        Naam brand/business se match karta hai (website / docs). Meta review karega —
                        max ~10 changes / 30 days.
                      </span>
                    </label>
                    <div className="infra-actions" style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className="btn dash-primary"
                        disabled={!brand.can_submit || busy === "brand-submit" || busy === "brand"}
                        onClick={submitBrand}
                      >
                        {busy === "brand-submit" ? "Submitting…" : "Submit to Meta for review"}
                      </button>
                    </div>

                    <h3 className="h3" style={{ marginTop: 20 }}>2. Apply approved name (re-register)</h3>
                    {brand.needs_register ? (
                      <div className="okbox" style={{ marginBottom: 10 }}>
                        Meta ne naam approve kar diya. Ab 6-digit WhatsApp two-step PIN se Apply karo
                        (14 din ke andar). PIN save nahi hota.
                      </div>
                    ) : (
                      <p className="lede">
                        Jab pending status <b>APPROVED</b> ho (ya webhook APPROVED aaye), yahan PIN se apply karoge.
                      </p>
                    )}
                    <div className="field" style={{ maxWidth: 220 }}>
                      <label>Two-step PIN</label>
                      <input
                        type="password"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        value={brandPin}
                        onChange={(e) => setBrandPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        placeholder="••••••"
                        disabled={!brand.needs_register || busy === "brand-register"}
                      />
                    </div>
                    <div className="infra-actions" style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className="btn dash-primary"
                        disabled={!brand.needs_register || busy === "brand-register" || String(brandPin).length !== 6}
                        onClick={applyBrand}
                      >
                        {busy === "brand-register" ? "Applying…" : "Apply approved name"}
                      </button>
                    </div>

                    {brand.history?.length > 0 && (
                      <>
                        <h3 className="h3" style={{ marginTop: 20 }}>History</h3>
                        <ul className="wh-brand-history">
                          {brand.history.map((h, i) => (
                            <li key={`${h.at}-${i}`}>
                              <span className="mono">{h.at ? fmtTime(h.at) : "—"}</span>
                              {" · "}
                              {h.action || "event"}
                              {h.name ? ` · ${h.name}` : ""}
                              {h.decision ? ` · ${h.decision}` : ""}
                              {h.rejection_reason ? ` · ${h.rejection_reason}` : ""}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === "settings" && (
              <>
                <p className="lede">InfraDealer Admin se credentials paste karo — Integration ID, API Key, Secret autofill ho jayenge.</p>
                <div className="field full">
                  <label>Paste credentials</label>
                  <textarea
                    rows={4}
                    value={paste}
                    onChange={(e) => applyPaste(e.target.value, setPaste, setForm)}
                    placeholder={"Integration ID: whk_...\nAPI Key: idk_...\nSecret: ids_..."}
                  />
                  <div className="infra-actions" style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      className="btn small primary"
                      onClick={() => applyPaste(paste, setPaste, setForm)}
                    >
                      Autofill
                    </button>
                    <button type="button" className="btn small dash-primary" disabled={busy === "save"} onClick={saveSettings}>
                      Save Settings
                    </button>
                  </div>
                </div>
                <div className="form-grid">
                  <div className="field full">
                    <label>InfraDealer API Base URL</label>
                    <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder={API_CONTRACT.baseUrl} />
                  </div>
                  <div className="field">
                    <label>Integration ID</label>
                    <input className="mono" value={form.integration_id} onChange={(e) => setForm({ ...form, integration_id: e.target.value })} placeholder="whk_..." />
                  </div>
                  <SecretField
                    label="API Key"
                    value={form.api_key}
                    onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    placeholder={cfg.api_key_masked || "idk_..."}
                  />
                  <SecretField
                    label="API Secret"
                    value={form.api_secret}
                    onChange={(e) => setForm({ ...form, api_secret: e.target.value })}
                    placeholder={cfg.api_secret_set ? "••••••••" : "ids_..."}
                  />
                  <div className="field">
                    <label>API Version</label>
                    <input value={form.api_version} onChange={(e) => setForm({ ...form, api_version: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>Mode</label>
                    <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
                      <option value="LIVE">LIVE</option>
                      <option value="TEST">TEST</option>
                    </select>
                  </div>
                </div>
                <h3 className="h3">Events</h3>
                <div className="infra-events">
                  {EVENT_LABELS.map(([key, label]) => (
                    <label key={key} className="infra-event-toggle">
                      <input
                        type="checkbox"
                        checked={!!cfg.event_flags?.[key]}
                        onChange={(e) => toggleEvent(key, e.target.checked)}
                      />
                      <span>{label}</span>
                      <span className="infra-event-state">{cfg.event_flags?.[key] ? "ON" : "OFF"}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </div>
        </section>
      </div>

      {detail && (
        <div className="infra-modal" role="dialog" aria-modal="true">
          <div className="infra-modal-box panel wide">
            <div className="tbl-head">
              <h3>Request Detail — {detail.request_id}</h3>
              <button type="button" className="btn small" onClick={() => setDetail(null)}>Close</button>
            </div>
            <pre className="infra-pre">{JSON.stringify(detail, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
