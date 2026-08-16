import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtTime } from "../api.js";

function Table({ id, title, placeholder, columns, rows, searchCols, actions, exportKind }) {
  const [q, setQ] = useState("");
  const filtered = rows.filter((row) => {
    if (!q) return true;
    return (row.search || "").toLowerCase().includes(q.toLowerCase());
  });
  return (
    <div className="tblwrap">
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
          {!filtered.length && <tr><td className="tbl-empty" colSpan={columns.length}>Koi record nahi</td></tr>}
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
  const [data, setData] = useState({
    messages: [], chats: [], submissions: [], otps: [], products: [], users: [], blocked: [], broadcasts: [],
  });
  const [blockNum, setBlockNum] = useState("");
  const [blockErr, setBlockErr] = useState("");
  const [err, setErr] = useState("");

  async function refresh() {
    const [statsRow, messages, chats, submissions, otps, products, users, blocked, broadcasts] = await Promise.all([
      api.stats(),
      api.admin("messages"),
      api.admin("chats"),
      api.admin("submissions"),
      api.admin("otps"),
      api.admin("products"),
      api.admin("users"),
      api.admin("blocked"),
      api.admin("broadcasts"),
    ]);
    setStats(statsRow);
    setData({ messages, chats, submissions, otps, products, users, blocked, broadcasts });
  }

  useEffect(() => {
    refresh().catch((e) => setErr(e.message));
  }, []);

  if (!stats) return err ? <div className="err">{err}</div> : <p className="lede">Admin load ho raha hai…</p>;

  return (
    <>
      <div className="label">Admin Panel · tracking ledger</div>
      <h1 className="display" style={{ fontSize: 32 }}>Tracking Ledger</h1>
      <p className="lede">Har message, OTP, submission aur publish action ka record — search, delete, export. Data SQLite backend se aata hai, seed data nahi hai.</p>
      {err && <div className="err">{err}</div>}
      <div className="admin-grid">
        {[
          [stats.messages, "WhatsApp Messages"],
          [stats.chats, "Chats"],
          [stats.submissions, "Form Submissions"],
          [stats.otps, "OTP Logs"],
          [stats.products, "Product Cards", true],
          [stats.users, "Users"],
          [stats.blocked, "Blocked Numbers"],
        ].map(([n, l, red]) => (
          <div className={`stat${red ? " red" : ""}`} key={l}><div className="n mono">{n}</div><div className="l">{l}</div></div>
        ))}
      </div>

      <Table id="chats" title="Chats" placeholder="search naam / number / text..." exportKind="chats" columns={["Time", "Conv", "From", "Dir", "Message", "Status"]} searchCols={[1, 2, 4]}
        rows={data.chats.map((c) => ({
          id: c.id,
          raw: c,
          search: `${c.conversation_id} ${c.from} ${c.from_name} ${c.body}`,
          cells: [
            <span className="mono">{fmtTime(c.created_at)}</span>,
            <span className="mono">{c.conversation_id}</span>,
            <span className="mono">+91 {c.from}{c.from_name ? ` (${c.from_name})` : ""}</span>,
            c.direction === "outbound" ? <span className="status published">out</span> : <span className="status draft">in</span>,
            (c.body || "").slice(0, 80),
            c.status,
          ],
        }))} />

      <Table id="msgs" title="WhatsApp Messages" placeholder="search number / ref / text..." exportKind="msgs" columns={["Time", "From", "Ref", "Message", "Parsed"]} searchCols={[1, 2, 3, 4]}
        rows={data.messages.map((m) => ({
          id: m.id,
          search: `${m.from} ${m.ref} ${m.text} ${m.parsed}`,
          cells: [
            <span className="mono">{fmtTime(m.created_at)}</span>,
            <span className="mono">+91 {m.from}</span>,
            <span className="mono">{m.ref}</span>,
            (m.text || "").slice(0, 90),
            <>{m.parsed} {m.ref && <Link to={`/list?ref=${m.ref}`} style={{ textDecoration: "underline" }}>form →</Link>}</>,
          ],
        }))} />

      <Table id="subs" title="Form Submissions" placeholder="search name / mobile / ref..." exportKind="subs" columns={["Time", "Ref", "Name", "Mobile", "Product", "Consent", "Status", "Dup", ""]} searchCols={[1, 2, 3, 4]}
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

      <Table id="otps" title="OTP Logs" placeholder="search mobile / status..." exportKind="otps" columns={["Time", "Mobile", "Status", "Attempts", "Expires"]} searchCols={[1, 2]}
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

      <Table id="prods" title="Product Cards" placeholder="search title / seller / mobile..." exportKind="prods" columns={["Time", "Ref", "Title", "Price", "Seller", "Mobile", "Status", "Spam", ""]} searchCols={[1, 2, 3, 4, 5]}
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

      <Table id="users" title="Users" placeholder="search name / mobile..." exportKind="users" columns={["User", "Mobile", "Source", "Joined", "Cards"]} searchCols={[0, 1]}
        rows={data.users.map((u) => ({
          id: u.id,
          search: `${u.name} ${u.mobile}`,
          cells: [u.name, <span className="mono">+91 {u.mobile}</span>, u.source, <span className="mono">{fmtTime(u.created_at)}</span>, <span className="mono">{u.cards}</span>],
        }))} />

      <div className="tblwrap">
        <div className="tbl-head">
          <h3>Blocked Numbers</h3>
          <div className="tools">
            <input type="search" placeholder="10-digit number..." readOnly value="" style={{ display: "none" }} />
            <a className="btn small" href={api.exportUrl("blocked")}>Export CSV</a>
          </div>
        </div>
        {blockErr && <div className="err">{blockErr}</div>}
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
          <tfoot>
            <tr>
              <td><input value={blockNum} onChange={(e) => setBlockNum(e.target.value)} placeholder="e.g. 9999999999" inputMode="numeric" style={{ border: "1px solid var(--line)", padding: "6px 10px", width: 180 }} /></td>
              <td>
                <button className="btn small" type="button" onClick={async () => {
                  try {
                    if (!window.confirm("Is number ko block karna confirm hai?")) return;
                    await api.block(blockNum);
                    setBlockNum("");
                    setBlockErr("");
                    refresh();
                  } catch (e) { setBlockErr(e.message); }
                }}>Block Number</button>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <Table id="bc" title="Broadcasts" placeholder="search message / number..." exportKind="bc" columns={["Time", "Message", "Recipients", "Delivered", "Status"]} searchCols={[1, 2]}
        rows={data.broadcasts.map((b) => ({
          id: b.id,
          search: `${b.message} ${(b.recipients || []).join(" ")}`,
          cells: [
            <span className="mono">{fmtTime(b.created_at)}</span>,
            (b.message || "").slice(0, 80),
            <span className="mono">{(b.recipients || []).join(", ")}</span>,
            <span className="mono">{b.delivered}/{b.total}</span>,
            <span className="status published">sent</span>,
          ],
        }))} />
    </>
  );
}
