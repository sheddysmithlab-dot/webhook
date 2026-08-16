import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtTime } from "../api.js";

function Status({ ok, label }) {
  return <span className={`status ${ok ? "published" : "blocked"}`}>{ok ? "configured" : "missing"}</span>;
}

export default function Webhook() {
  const [s, setS] = useState(null);
  const [chats, setChats] = useState([]);
  const [q, setQ] = useState("");
  const [unread, setUnread] = useState(false);
  const [fromNum, setFromNum] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [result, setResult] = useState("");
  const [inboxMsg, setInboxMsg] = useState("");
  const [bcMsg, setBcMsg] = useState("");
  const [bcResult, setBcResult] = useState("");
  const [selected, setSelected] = useState([]);
  const [openConv, setOpenConv] = useState(null);
  const [inbound, setInbound] = useState({ from_mobile: "", name: "", text: "" });
  const [err, setErr] = useState("");

  async function loadSettings() {
    const row = await api.settings();
    setS(row);
  }
  async function loadChats(params) {
    const list = await api.chats(params || { q, unread, from_number: fromNum });
    setChats(list);
    const recs = [];
    const seen = {};
    list.forEach((c) => {
      if (c.direction !== "inbound" || seen[c.from]) return;
      seen[c.from] = true;
      recs.push(c.from);
    });
    setSelected(recs);
  }

  useEffect(() => {
    Promise.all([loadSettings(), loadChats({})]).catch((e) => setErr(e.message));
  }, []);

  const recipients = useMemo(() => {
    const seen = {};
    const out = [];
    chats.forEach((c) => {
      if (c.direction !== "inbound" || seen[c.from]) return;
      seen[c.from] = true;
      out.push({ from: c.from, name: c.from_name || "", last: c.body || "" });
    });
    return out;
  }, [chats]);

  function field(k, v) {
    setS((prev) => ({ ...prev, [k]: v }));
  }

  async function save() {
    try {
      const saved = await api.saveSettings({
        app_secret: s.app_secret,
        app_id: s.app_id,
        waba_id: s.waba_id,
        phone_number_id: s.phone_number_id,
        system_user_token: s.system_user_token,
        graph_version: s.graph_version,
        test_recipient: s.test_recipient,
        field_messages: s.webhook_fields.messages,
        field_template_status: s.webhook_fields.message_template_status_update,
        field_account_alerts: s.webhook_fields.account_alerts,
      });
      setS(saved);
      setStatusMsg("Settings save ho gayi (SQLite).");
    } catch (e) {
      setStatusMsg(e.message);
    }
  }

  async function regen() {
    const saved = await api.regenToken();
    setS(saved);
  }

  async function testChallenge(ok) {
    const token = ok ? s.verify_token : "wrong-token";
    const challenge = "challenge_local";
    const url = `/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=${encodeURIComponent(token)}&hub.challenge=${challenge}`;
    const res = await fetch(url);
    const text = await res.text();
    if (res.ok) {
      setResult(`Verify Challenge OK · HTTP ${res.status}\nGET ${s.callback_url}?hub.mode=subscribe&hub.verify_token=${token}&hub.challenge=${challenge}\n\n→ ${text}`);
    } else {
      setResult(`HTTP ${res.status} Forbidden\nGET ...verify_token=${token}\n\n→ ${text}`);
    }
  }

  if (!s) return err ? <div className="err">{err}</div> : <p className="lede">Settings load ho rahi hain…</p>;

  return (
    <>
      <div className="label">WhatsApp Webhook · Meta Cloud API</div>
      <h1 className="display" style={{ fontSize: 32 }}>WhatsApp Webhook + Chats</h1>
      <p className="lede">Callback URL Meta console mein paste karo. Incoming messages is Python webhook par aate hain, parse hote hain, aur listing form ke liye reference milta hai.</p>
      {s && (
        <div className="okbox" style={{ marginBottom: "var(--space-5)" }}>
          <b>Meta Connect values (live)</b>
          <div style={{ marginTop: 8, fontSize: 13 }}>
            <div><b>Callback URL:</b> <span className="mono">{s.callback_url}</span></div>
            <div style={{ marginTop: 4 }}><b>Verify Token:</b> <span className="mono">{s.verify_token}</span></div>
          </div>
          <p className="hint" style={{ marginTop: 8 }}>
            Meta → your app → <b>WhatsApp → Configuration</b> → Webhook → Edit → paste dono values → Verify and save → Subscribe <b>messages</b>.
            Phir App ID / App Secret / Phone Number ID / WABA ID / System User Token yahan save karo.
          </p>
        </div>
      )}
      {err && <div className="err">{err}</div>}
      <div className="meta-layout">
        <div className="meta-grid">
          <div className="meta-panel">
            <h2>Webhook Settings (Meta Console)</h2>
            <div className="set-grid">
              <div className="field full">
                <label>Callback URL *</label>
                <input readOnly value={s.callback_url} />
                <span className="hint">Meta → WhatsApp → Configuration mein paste karo.</span>
              </div>
              <div className="field full">
                <label>Verify Token *</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <input readOnly value={s.verify_token} style={{ flex: 1 }} />
                  <button className="btn small" type="button" onClick={regen}>Regenerate</button>
                </div>
              </div>
              <div className="field"><label>App Secret</label><input type="password" value={s.app_secret} onChange={(e) => field("app_secret", e.target.value)} autoComplete="off" /></div>
              <div className="field"><label>App ID</label><input value={s.app_id} onChange={(e) => field("app_id", e.target.value)} /></div>
              <div className="field"><label>WABA ID</label><input value={s.waba_id} onChange={(e) => field("waba_id", e.target.value)} /></div>
              <div className="field"><label>Phone Number ID</label><input value={s.phone_number_id} onChange={(e) => field("phone_number_id", e.target.value)} /></div>
              <div className="field full">
                <label>System User Token</label>
                <input type="password" value={s.system_user_token} onChange={(e) => field("system_user_token", e.target.value)} autoComplete="off" />
              </div>
              <div className="field">
                <label>Graph API Version</label>
                <select value={s.graph_version} onChange={(e) => field("graph_version", e.target.value)}>
                  <option>v23.0</option><option>v22.0</option><option>v21.0</option>
                </select>
              </div>
              <div className="field">
                <label>Test Recipient</label>
                <input inputMode="numeric" value={s.test_recipient} onChange={(e) => field("test_recipient", e.target.value)} />
              </div>
            </div>
            <div style={{ margin: "var(--space-4) 0 0" }}>
              <span className="label" style={{ display: "block", marginBottom: 6 }}>Webhook Fields</span>
              <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, marginBottom: 4 }}>
                <input type="checkbox" checked={s.webhook_fields.messages} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, messages: e.target.checked } })} /> messages
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, marginBottom: 4 }}>
                <input type="checkbox" checked={s.webhook_fields.message_template_status_update} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, message_template_status_update: e.target.checked } })} /> message_template_status_update
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
                <input type="checkbox" checked={s.webhook_fields.account_alerts} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, account_alerts: e.target.checked } })} /> account_alerts
              </label>
            </div>
            <div className="inline-actions"><button className="btn primary" type="button" onClick={save}>Save Settings</button></div>
            {statusMsg && <div className="okbox" style={{ margin: "var(--space-4) 0 0" }}>{statusMsg}</div>}
          </div>
          <div className="meta-panel">
            <h2>Webhook Status + Test</h2>
            <div className="meta-status">
              <div className="st"><span>Callback URL</span><Status ok={s.configured.callback_url} /></div>
              <div className="st"><span>Verify Token</span><Status ok={s.configured.verify_token} /></div>
              <div className="st"><span>App Secret</span><Status ok={s.configured.app_secret} /></div>
              <div className="st"><span>WABA ID</span><Status ok={s.configured.waba_id} /></div>
              <div className="st"><span>Phone Number ID</span><Status ok={s.configured.phone_number_id} /></div>
              <div className="st"><span>System User Token</span><Status ok={s.configured.system_user_token} /></div>
              <div className="st"><span>Graph API Version</span><span className="mono">{s.graph_version}</span></div>
              <div className="st"><span>Webhook Subscribe</span><span className={`status ${s.subscribed ? "published" : "draft"}`}>{s.subscribed ? "subscribed" : "not subscribed"}</span></div>
            </div>
            <div className="inline-actions">
              <button className="btn" type="button" onClick={() => testChallenge(true)}>Test Webhook (Verify Challenge)</button>
              <button className="btn" type="button" onClick={() => testChallenge(false)}>Test Mismatch (403)</button>
              <button className="btn" type="button" onClick={async () => { try { const r = await api.subscribe(); setResult(JSON.stringify(r, null, 2)); loadSettings(); } catch (e) { setResult(e.message); } }}>Subscribe Webhook</button>
              <button className="btn" type="button" onClick={async () => { try { const r = await api.testMessage(); setResult(JSON.stringify(r, null, 2)); loadChats(); } catch (e) { setResult(e.message); } }}>Send Test Message</button>
            </div>
            {result && <pre className="payload">{result}</pre>}
            <div className="hint" style={{ marginTop: 10 }}>Last delivery: {s.last_delivery || "—"}</div>
          </div>
        </div>

        <div className="meta-panel">
          <h2>Chats Inbox</h2>
          <p className="hint" style={{ margin: "0 0 var(--space-3)" }}>Production: Meta POST /webhook/whatsapp. Local test ke liye neeche inbound form use karo — sample chats load nahi hote.</p>
          <div className="form-grid">
            <div className="field"><label>From mobile</label><input value={inbound.from_mobile} onChange={(e) => setInbound({ ...inbound, from_mobile: e.target.value })} placeholder="9876543210" /></div>
            <div className="field"><label>Name</label><input value={inbound.name} onChange={(e) => setInbound({ ...inbound, name: e.target.value })} placeholder="Customer" /></div>
            <div className="field full"><label>Message</label><textarea rows="2" value={inbound.text} onChange={(e) => setInbound({ ...inbound, text: e.target.value })} placeholder="Honda Activa 6G 2021, price 45000, good, Delhi" /></div>
          </div>
          <div className="inline-actions">
            <button className="btn primary" type="button" onClick={async () => {
              try {
                const r = await api.inbound(inbound);
                setInboxMsg(`Inbound save · ref ${r.ref}`);
                setInbound({ from_mobile: "", name: "", text: "" });
                loadChats();
              } catch (e) { setInboxMsg(e.message); }
            }}>Inbound Message Save</button>
            <button className="btn" type="button" onClick={() => loadChats()}>Refresh Inbox</button>
            <button className="btn" type="button" onClick={async () => {
              try {
                const r = await api.toAdmin();
                setInboxMsg(`Admin ledger mein ${r.pushed} messages.`);
              } catch (e) { setInboxMsg(e.message); }
            }}>Admin Panel mein bhejo</button>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: "var(--space-4)" }}>
            <input type="search" placeholder="Search naam / number / text..." value={q} onChange={(e) => setQ(e.target.value)} style={{ border: "1px solid var(--line)", padding: "8px 12px", borderRadius: 8, minWidth: 220, flex: 1 }} />
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Unread only</label>
            <input placeholder="From number" value={fromNum} onChange={(e) => setFromNum(e.target.value)} style={{ border: "1px solid var(--line)", padding: "8px 12px", borderRadius: 8, width: 150 }} />
            <button className="btn small" type="button" onClick={() => loadChats({ q, unread, from_number: fromNum })}>Filter</button>
          </div>
          {inboxMsg && <div className="okbox" style={{ marginTop: 12 }}>{inboxMsg} {inboxMsg.includes("Admin") && <p style={{ marginTop: 8 }}><Link className="btn small" to="/admin">Admin Panel kholo →</Link></p>}</div>}
          <div className="inbox">
            {!chats.length && <div className="empty" style={{ padding: "var(--space-5)", textAlign: "center" }}>Inbox khali hai — webhook ya inbound form se message aana chahiye.</div>}
            {chats.map((c) => (
              <div className="chat-row" key={c.id} role="button" tabIndex={0} onClick={() => setOpenConv(openConv === c.id ? null : c.id)}>
                <div className="cr-top">
                  <span className="cr-name">{c.unread && <span className="unread-dot" />}{c.from_name || "Unknown"}</span>
                  <span className="cr-num">+91 {c.from} {c.direction === "outbound" ? "→" : "←"}</span>
                  <span className="cr-time">{fmtTime(c.timestamp ? new Date(c.timestamp).toISOString() : c.created_at)}</span>
                </div>
                <div className="cr-prev">{(c.body || "").slice(0, 90)}</div>
                {openConv === c.id && (
                  <div className="chat-thread" style={{ display: "flex" }}>
                    <div className={`tbub ${c.direction === "outbound" ? "out" : "in"}`}>
                      {c.body}
                      <div className="tmeta">{c.direction} · {c.status} · {(c.wamid || "").slice(0, 22)}</div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="meta-panel">
          <h2>Broadcast Message</h2>
          <p className="hint" style={{ margin: "0 0 var(--space-3)" }}>Saved Phone Number ID + token se Graph API call hoti hai. Recipients inbound chats se aate hain.</p>
          <div className="inline-actions">
            <button className="btn small" type="button" onClick={() => setSelected(recipients.map((r) => r.from))}>Select All</button>
            <button className="btn small" type="button" onClick={() => setSelected([])}>Clear</button>
            <span className="hint" style={{ alignSelf: "center" }}>{selected.length} selected</span>
          </div>
          <div className="bc-list">
            {!recipients.length && <div className="empty" style={{ padding: "var(--space-4)", textAlign: "center" }}>Koi customer chat nahi.</div>}
            {recipients.map((r) => (
              <label className="bc-item" key={r.from}>
                <input type="checkbox" checked={selected.includes(r.from)} onChange={(e) => setSelected(e.target.checked ? [...selected, r.from] : selected.filter((x) => x !== r.from))} value={r.from} />
                <span><b>{r.name || "Unknown"}</b> <span className="mono bc-meta">+91 {r.from}</span><br /><span className="bc-meta">{r.last.slice(0, 60)}</span></span>
              </label>
            ))}
          </div>
          <div className="field" style={{ marginTop: "var(--space-4)" }}>
            <label htmlFor="bc_msg">Message *</label>
            <textarea id="bc_msg" rows="3" value={bcMsg} onChange={(e) => setBcMsg(e.target.value)} placeholder="Message likho…" />
          </div>
          <button className="btn primary" type="button" onClick={async () => {
            try {
              const r = await api.broadcast({ message: bcMsg, recipients: selected });
              setBcResult(`Broadcast ${r.delivered}/${r.total} delivered.`);
              setBcMsg("");
              loadChats();
            } catch (e) { setBcResult(e.message); }
          }}>Send Broadcast</button>
          {bcResult && <div className={bcResult.startsWith("Broadcast") ? "okbox" : "err"} style={{ marginTop: 12 }}>{bcResult}</div>}
        </div>
      </div>
    </>
  );
}
