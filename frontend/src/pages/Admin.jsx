import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api, fmtTime, isAdminAuthError } from "../api.js";

const TABS = [
  { id: "ai", label: "AI Review", countKey: "ai_drafts" },
  { id: "contacts", label: "Contacts", countKey: "contacts" },
  { id: "broadcasts", label: "Broadcast Msg", countKey: "contacts" },
  { id: "submissions", label: "Submissions", countKey: "submissions" },
  { id: "otps", label: "OTP Logs", countKey: "otps" },
  { id: "products", label: "Products", countKey: "products" },
  { id: "users", label: "Users", countKey: "users" },
  { id: "blocked", label: "Blocked", countKey: "blocked" },
];

function Table({ title, placeholder, columns, rows, actions, exportKind }) {
  const [q, setQ] = useState("");
  const filtered = rows.filter((row) => {
    if (!q) return true;
    return (row.search || "").toLowerCase().includes(q.toLowerCase());
  });
  const colCount = columns.length;
  return (
    <div className="tblwrap tab-panel">
      <div className="tbl-head">
        <h3>{title}</h3>
        <div className="tools">
          <input type="search" aria-label={`${title} search`} placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)} />
          <a className="btn small" href={api.exportUrl(exportKind)}>Export CSV</a>
        </div>
      </div>
      <table>
        <thead>
          <tr>{columns.map((c) => <th scope="col" key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {!filtered.length && <tr><td className="tbl-empty" colSpan={colCount}>Koi record nahi</td></tr>}
          {filtered.map((row, idx) => (
            <tr key={row.id || idx}>
              {row.cells.map((cell, i) => <td key={i}>{cell}</td>)}
              {actions && <td>{actions(row.raw)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Admin() {
  const [stats, setStats] = useState(null);
  const [tab, setTab] = useState("ai");
  const [data, setData] = useState({
    contacts: [], submissions: [], otps: [], products: [], users: [], blocked: [], broadcasts: [], aiDrafts: [],
  });
  const [aiOpen, setAiOpen] = useState(null);
  const [blockNum, setBlockNum] = useState("");
  const [blockErr, setBlockErr] = useState("");
  const [bcMsg, setBcMsg] = useState("");
  const [bcBusy, setBcBusy] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    const [statsRow, contacts, submissions, otps, products, users, blocked, broadcasts, aiDrafts] = await Promise.all([
      api.stats(),
      api.admin("contacts"),
      api.admin("submissions"),
      api.admin("otps"),
      api.admin("products"),
      api.admin("users"),
      api.admin("blocked"),
      api.admin("broadcasts"),
      api.aiDrafts(),
    ]);
    setStats(statsRow);
    setData({ contacts, submissions, otps, products, users, blocked, broadcasts, aiDrafts });
  }

  useEffect(() => {
    refresh().catch((e) => setErr(e.message));
  }, []);

  if (!stats) {
    if (err && isAdminAuthError(err)) {
      return <Navigate to="/login" replace />;
    }
    return err ? <div className="err">{err}</div> : <p className="lede">Admin load ho raha hai…</p>;
  }

  return (
    <>
      <div className="wh-crumb">Admin</div>
      <div className="wh-head" style={{ marginBottom: 18 }}>
        <div>
          <h1>Tracking Ledger</h1>
          <p>Contacts WhatsApp chat se auto-save hote hain — Meta profile name + number. Broadcast se sab contacts ko ek saath message bhejo.</p>
        </div>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="admin-grid">
        {TABS.map((item) => (
          <button
            type="button"
            className={`stat${item.id === "ai" || item.id === "products" ? " red" : ""}${tab === item.id ? " on" : ""}`}
            key={item.id}
            onClick={() => setTab(item.id)}
          >
            <div className="n mono">{stats[item.countKey] ?? 0}</div>
            <div className="l">{item.label}</div>
          </button>
        ))}
      </div>

      <div className="admin-tabs" role="tablist" aria-label="Admin tables">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={tab === item.id}
            aria-controls={`panel-${item.id}`}
            className={`admin-tab${tab === item.id ? " on" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
            <span className="tab-count">{stats[item.countKey] ?? 0}</span>
          </button>
        ))}
      </div>

      <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "ai" && (
          <div className="tblwrap tab-panel">
            <div className="tbl-head">
              <h3>AI Review · confirmed JSON</h3>
              <div className="tools">
                <span className="hint">{stats.ai_pending || 0} pending review</span>
                <a className="btn small" href={api.exportUrl("ai")}>Export CSV</a>
              </div>
            </div>
            <p className="ai-note">Customer WhatsApp pe Haan/Yes confirm kare tabhi yahan final JSON aati hai. List pe click karke JSON dekho; Post listing se card photos ke saath Listing page pe live hota hai.</p>
            <table>
              <thead>
                <tr>
                  <th>Time</th><th>Customer</th><th>Vehicle</th><th>Rate</th><th>Location</th><th>Status</th><th></th>
                </tr>
              </thead>
              <tbody>
                {!data.aiDrafts.length && <tr><td className="tbl-empty" colSpan={7}>Confirmed listing nahi — customer Haan/Yes ke baad yahan aayegi</td></tr>}
                {data.aiDrafts.map((d) => {
                  const c = d.confirmed || {};
                  return (
                  <tr key={d.id} className="ai-row" onClick={async () => setAiOpen(await api.aiDraft(d.id))}>
                    <td className="mono">{fmtTime(d.created_at)}</td>
                    <td>{d.name || "—"}<div className="mono">+91 {d.mobile}</div></td>
                    <td>{c.vehicle || d.title || "—"}</td>
                    <td>{c.rate || "—"}</td>
                    <td>{c.location || "—"}</td>
                    <td><span className={`status ${d.status === "POSTED" ? "published" : d.status === "REJECTED" ? "blocked" : "draft"}`}>{d.status}</span></td>
                    <td>
                      <button className="btn primary small" type="button" disabled={d.status === "POSTED"} onClick={async (e) => {
                        e.stopPropagation();
                        if (!window.confirm("Is confirmed JSON ko Listing page pe photo ke saath post karein?")) return;
                        await api.aiDraftPost(d.id);
                        await refresh();
                        window.location.href = "/";
                      }}>Post listing</button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
            {aiOpen && (
              <div className="ai-detail">
                <div className="tbl-head">
                  <h3>Final JSON · {aiOpen.confirmed?.vehicle || aiOpen.draft?.title || aiOpen.mobile}</h3>
                  <button className="btn small" type="button" onClick={() => setAiOpen(null)}>Close</button>
                </div>
                <div className="ai-grid">
                  <div>
                    <div className="label">Customer-confirmed JSON</div>
                    <pre className="payload">{JSON.stringify(aiOpen.confirmed || aiOpen.draft?.confirmed || {}, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="label">Photos</div>
                    <div className="ai-media">
                      {(aiOpen.media || []).filter((m) => m.has_file || m.url).length === 0 && <span className="hint">No photos</span>}
                      {(aiOpen.media || []).map((m) => (
                        m.url || m.has_file
                          ? <a key={m.id} href={m.url || api.aiMediaUrl(m.id)} target="_blank" rel="noreferrer">
                              {m.kind === "image"
                                ? <img src={m.url || api.aiMediaUrl(m.id)} alt="" />
                                : `${m.kind} #${m.id}`}
                            </a>
                          : <span key={m.id}>{m.kind} #{m.id}</span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="inline-actions">
                  <button className="btn small" type="button" onClick={async () => { await api.aiDraftStatus(aiOpen.draft.id, "NEEDS_INFO"); setAiOpen(await api.aiDraft(aiOpen.draft.id)); refresh(); }}>Needs info</button>
                  <button className="btn small" type="button" onClick={async () => {
                    const reason = window.prompt("Reject reason (WhatsApp pe user ko jayega):", "") || "";
                    if (!reason.trim()) {
                      window.alert("Reject reason zaroori hai.");
                      return;
                    }
                    const res = await api.aiDraftStatus(aiOpen.draft.id, "REJECTED", reason.trim());
                    if (res && res.notified === false) {
                      window.alert(`Listing reject ho gayi, lekin WhatsApp nahi gaya.\n${res.notify_error || "Graph/WhatsApp error"}`);
                    }
                    setAiOpen(await api.aiDraft(aiOpen.draft.id));
                    refresh();
                  }}>Reject</button>
                  {(aiOpen.draft?.status === "REJECTED" || aiOpen.draft?.status === "POSTED") && (
                    <button className="btn small" type="button" onClick={async () => {
                      const note = aiOpen.draft?.status === "REJECTED"
                        ? (window.prompt("Resend reason (blank = saved reason):", "") || "")
                        : "";
                      const res = await api.aiDraftResendDecision(aiOpen.draft.id, note.trim());
                      if (res?.notified) window.alert("WhatsApp bhej diya.");
                      else window.alert(`WhatsApp fail:\n${res?.notify_error || "unknown"}`);
                      setAiOpen(await api.aiDraft(aiOpen.draft.id));
                      refresh();
                    }}>Resend WhatsApp</button>
                  )}
                  <button className="btn primary small" type="button" disabled={aiOpen.draft?.status === "POSTED"} onClick={async () => {
                    if (!window.confirm("Listing page pe card photos ke saath post karni hai?")) return;
                    await api.aiDraftPost(aiOpen.draft.id);
                    await refresh();
                    window.location.href = "/";
                  }}>Post listing</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "contacts" && (
          <Table title="WhatsApp Contacts" placeholder="search name / mobile / city..." exportKind="contacts" columns={["Name", "Mobile", "City", "Messages", "Last Message"]}
            rows={data.contacts.map((c) => ({
              id: c.id,
              search: `${c.name} ${c.mobile} ${c.city}`,
              cells: [
                c.name || "—",
                <span className="mono">+91 {c.mobile}</span>,
                c.city || "—",
                <span className="mono">{c.messages || 0}</span>,
                <span className="mono">{fmtTime(c.last_at)}</span>,
              ],
            }))} />
        )}

        {tab === "submissions" && (
          <Table title="Form Submissions" placeholder="search name / mobile / ref..." exportKind="subs" columns={["Time", "Ref", "Name", "Mobile", "Product", "Consent", "Status", "Dup", ""]}
            rows={data.submissions.map((s) => ({
              id: s.id,
              raw: s,
              search: `${s.ref} ${s.name} ${s.mobile} ${s.title}`,
              cells: [
                <span className="mono">{fmtTime(s.created_at)}</span>,
                <span className="mono">{s.ref}</span>,
                s.name,
                <span className="mono">+91 {s.mobile}</span>,
                s.title,
                s.consent ? <span className="status published">yes</span> : <span className="status draft">no</span>,
                <span className={`status ${s.status}`}>{s.status}</span>,
                s.dup_flag ? <span className="status draft">dup</span> : "—",
              ],
            }))}
            actions={(s) => <button className="btn small" type="button" onClick={async () => { await api.deleteSubmission(s.id); refresh(); }}>Delete</button>}
          />
        )}

        {tab === "otps" && (
          <Table title="OTP Logs" placeholder="search mobile / status..." exportKind="otps" columns={["Time", "Mobile", "Status", "Attempts", "Expires"]}
            rows={data.otps.map((o) => ({
              id: o.id,
              search: `${o.mobile} ${o.status}`,
              cells: [
                <span className="mono">{fmtTime(o.created_at)}</span>,
                <span className="mono">+91 {o.mobile}</span>,
                <span className={`status ${o.status}`}>{o.status}</span>,
                <span className="mono">{o.attempts}/{o.max_attempts}</span>,
                <span className="mono">{o.expires_at ? new Date(o.expires_at).toLocaleTimeString("en-IN") : "—"}</span>,
              ],
            }))} />
        )}

        {tab === "products" && (
          <Table title="Product Cards" placeholder="search title / seller / mobile..." exportKind="prods" columns={["Time", "Ref", "Title", "Price", "Seller", "Mobile", "Status", "Spam", ""]}
            rows={data.products.map((p) => ({
              id: p.id,
              raw: p,
              search: `${p.ref} ${p.title} ${p.seller_name} ${p.mobile}`,
              cells: [
                <span className="mono">{fmtTime(p.created_at)}</span>,
                <span className="mono">{p.ref}</span>,
                p.title,
                <span className="mono">₹{p.price}</span>,
                p.seller_name,
                <span className="mono">+91 {p.mobile}</span>,
                <span className={`status ${p.status}`}>{p.status}</span>,
                p.spam_flags ? <span className="status blocked">{p.spam_flags}</span> : "—",
              ],
            }))}
            actions={(p) => (
              <>
                <button className="btn small" type="button" onClick={async () => { await api.toggleProduct(p.id); refresh(); }}>{p.status === "published" ? "Unpublish" : "Publish"}</button>{" "}
                <button className="btn small" type="button" onClick={async () => { await api.deleteProduct(p.id); refresh(); }}>Delete</button>
              </>
            )}
          />
        )}

        {tab === "users" && (
          <Table title="Users" placeholder="search name / mobile / broker / user..." exportKind="users" columns={["User", "Mobile", "Category", "Source", "Joined", "Cards"]}
            rows={data.users.map((u) => ({
              id: u.id,
              search: `${u.name} ${u.mobile} ${u.role || ""}`,
              cells: [
                u.name,
                <span className="mono">+91 {u.mobile}</span>,
                <span className={`status ${u.role === "broker" ? "draft" : "published"}`}>{(u.role || "user")}</span>,
                u.source,
                <span className="mono">{fmtTime(u.created_at)}</span>,
                <span className="mono">{u.cards}</span>,
              ],
            }))} />
        )}

        {tab === "blocked" && (
          <div className="tblwrap tab-panel">
            <div className="tbl-head">
              <h3>Blocked Numbers</h3>
              <div className="tools">
                <a className="btn small" href={api.exportUrl("blocked")}>Export CSV</a>
              </div>
            </div>
            {blockErr && <div className="err" style={{ margin: "12px 16px 0" }}>{blockErr}</div>}
            <div className="block-add">
              <input value={blockNum} onChange={(e) => setBlockNum(e.target.value)} placeholder="e.g. 9999999999" inputMode="numeric" />
              <button className="btn small" type="button" onClick={async () => {
                try {
                  if (!window.confirm("Is number ko block karna confirm hai?")) return;
                  await api.block(blockNum);
                  setBlockNum("");
                  setBlockErr("");
                  refresh();
                } catch (e) { setBlockErr(e.message); }
              }}>Block Number</button>
            </div>
            <table>
              <thead><tr><th>Number</th><th></th></tr></thead>
              <tbody>
                {!data.blocked.length && <tr><td className="tbl-empty" colSpan={2}>Koi blocked number nahi</td></tr>}
                {data.blocked.map((b) => (
                  <tr key={b.id}>
                    <td className="mono">+91 {b.mobile}</td>
                    <td><button className="btn small" type="button" onClick={async () => { await api.unblock(b.mobile); refresh(); }}>Unblock</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "broadcasts" && (
          <div className="tblwrap tab-panel">
            <div className="tbl-head">
              <h3>Broadcast Message</h3>
              <div className="tools">
                <span className="hint">{data.contacts.length} contacts ready</span>
                <a className="btn small" href={api.exportUrl("bc")}>Export CSV</a>
              </div>
            </div>
            <div className="block-add" style={{ display: "block", paddingTop: 10 }}>
              <textarea
                value={bcMsg}
                onChange={(e) => setBcMsg(e.target.value)}
                rows={4}
                placeholder="Sab contacts ko bhejne ke liye message likhiye..."
                style={{ width: "100%", marginBottom: 10 }}
              />
              <button
                className="btn primary small"
                type="button"
                disabled={bcBusy || !bcMsg.trim() || data.contacts.length === 0}
                onClick={async () => {
                  const recipients = Array.from(new Set((data.contacts || []).map((c) => c.mobile).filter(Boolean)));
                  if (!recipients.length) {
                    setErr("Koi valid contact nahi mila.");
                    return;
                  }
                  if (!window.confirm(`${recipients.length} contacts ko broadcast bhejna hai?`)) return;
                  setBcBusy(true);
                  try {
                    const out = await api.broadcast({ message: bcMsg.trim(), recipients });
                    setBcMsg("");
                    setErr(`Broadcast sent: ${out.delivered}/${out.total}`);
                    await refresh();
                  } catch (e) {
                    setErr(e.message);
                  } finally {
                    setBcBusy(false);
                  }
                }}
              >
                {bcBusy ? "Sending..." : "Send to all contacts"}
              </button>
            </div>
            <table>
              <thead>
                <tr><th>Time</th><th>Message</th><th>Recipients</th><th>Delivered</th><th>Status</th></tr>
              </thead>
              <tbody>
                {!data.broadcasts.length && <tr><td className="tbl-empty" colSpan={5}>Abhi tak koi broadcast nahi bheja gaya</td></tr>}
                {data.broadcasts.map((b) => (
                  <tr key={b.id}>
                    <td className="mono">{fmtTime(b.created_at)}</td>
                    <td>{(b.message || "").slice(0, 120)}</td>
                    <td className="mono">{(b.recipients || []).length}</td>
                    <td className="mono">{b.delivered}/{b.total}</td>
                    <td><span className="status published">sent</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
