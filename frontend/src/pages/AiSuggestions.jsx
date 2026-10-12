import { useEffect, useState } from "react";
import { api, fmtTime } from "../api.js";

const FILTERS = [
  { id: "PENDING", label: "Pending" },
  { id: "APPROVED", label: "Live rules" },
  { id: "CODE", label: "Code bugs" },
  { id: "DONE", label: "Rejected / band" },
];

const SEVERITY_CLASS = { high: "blocked", medium: "draft", low: "published" };

function matches(item, filter) {
  if (filter === "PENDING") return item.status === "PENDING" && item.kind === "prompt";
  if (filter === "APPROVED") return item.status === "APPROVED";
  if (filter === "CODE") return item.kind === "code" && item.status === "PENDING";
  return ["REJECTED", "DISABLED", "NOTED"].includes(item.status);
}

function SuggestionCard({ item, onAction }) {
  const [line, setLine] = useState(item.prompt_line || "");
  const [busy, setBusy] = useState(false);
  const pending = item.status === "PENDING";

  async function run(action) {
    setBusy(true);
    try {
      await onAction(item.id, action, line);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sg-card">
      <div className="sg-top">
        <strong>{item.title}</strong>
        <span className={`status ${SEVERITY_CLASS[item.severity] || "draft"}`}>{item.severity}</span>
        <span className="status draft">{item.kind === "code" ? "code bug" : "prompt"}</span>
        <span className="hint mono">chat …{item.conversation || "—"} · {fmtTime(item.created_at)}</span>
      </div>
      {item.problem && <p className="sg-problem">{item.problem}</p>}
      {item.evidence && <pre className="payload">{item.evidence}</pre>}
      {item.kind === "prompt" && (
        <>
          <div className="label">Prompt line {pending ? "(edit kar sakte hain)" : ""}</div>
          {pending
            ? <textarea className="sg-line" rows={2} maxLength={300} value={line} onChange={(e) => setLine(e.target.value)} />
            : <pre className="payload">{item.prompt_line}</pre>}
        </>
      )}
      <div className="inline-actions">
        {pending && item.kind === "prompt" && (
          <button className="btn primary small" type="button" disabled={busy || !line.trim()} onClick={() => run("approve")}>
            Approve → live prompt
          </button>
        )}
        {pending && item.kind === "code" && (
          <button className="btn small" type="button" disabled={busy} onClick={() => run("noted")}>Noted (code fix)</button>
        )}
        {pending && <button className="btn small" type="button" disabled={busy} onClick={() => run("reject")}>Reject</button>}
        {item.status === "APPROVED" && (
          <button className="btn small" type="button" disabled={busy} onClick={() => run("disable")}>Band karo</button>
        )}
        {!pending && item.reviewed_at && (
          <span className="hint">{item.status} · {fmtTime(item.reviewed_at)}{item.reviewed_by ? ` · ${item.reviewed_by}` : ""}</span>
        )}
      </div>
    </div>
  );
}

export default function AiSuggestions({ onChange }) {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState("PENDING");
  const [err, setErr] = useState("");

  async function load() {
    try {
      setData(await api.aiSuggestions());
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!data?.review?.running) return undefined;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [data?.review?.running]);

  async function onAction(id, action, line) {
    try {
      await api.aiSuggestionAction(id, action, line);
      await load();
      onChange?.();
    } catch (e) {
      setErr(e.message);
    }
  }

  async function runNow() {
    try {
      const out = await api.aiSuggestionsRun();
      if (!out.started) setErr("Review pehle se chal raha hai.");
      await load();
    } catch (e) {
      setErr(e.message);
    }
  }

  const items = data?.items || [];
  const shown = items.filter((item) => matches(item, filter));
  const live = items.filter((item) => item.status === "APPROVED").length;
  const last = data?.review?.last_result;

  return (
    <div className="tblwrap tab-panel">
      <div className="tbl-head">
        <h3>AI Suggestions · chat review</h3>
        <div className="tools">
          <span className="hint">{live}/{data?.max_rules ?? 15} live rules</span>
          <button className="btn small" type="button" disabled={!data || data.review?.running} onClick={runNow}>
            {data?.review?.running ? "Review chal raha hai…" : "Abhi review karo"}
          </button>
        </div>
      </div>
      <p className="ai-note">
        Z.AI har {data?.interval_hours ?? 6} ghante me nayi WhatsApp chats padh kar bug dhoondhta hai.
        Prompt line sirf Approve karne ke baad live agent ke prompt me judti hai. OTP, account, price, office aur admin wale rules locked hain.
        {last && last.ok && ` Pichhla review: ${last.reviewed} chats, ${last.added} naye suggestions.`}
      </p>
      {err && <div className="err" style={{ margin: "12px 16px 0" }}>{err}</div>}
      <div className="sg-filters">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" className={`admin-tab${filter === f.id ? " on" : ""}`} onClick={() => setFilter(f.id)}>
            {f.label}
            <span className="tab-count">{items.filter((item) => matches(item, f.id)).length}</span>
          </button>
        ))}
      </div>
      <div className="sg-list">
        {!data && !err && <p className="hint">Load ho raha hai…</p>}
        {data && !shown.length && <p className="tbl-empty">Is list me kuch nahi</p>}
        {shown.map((item) => <SuggestionCard key={`${item.id}-${item.status}`} item={item} onAction={onAction} />)}
      </div>
      {filter === "APPROVED" && data?.active_prompt && (
        <div className="ai-detail">
          <div className="label">Live prompt me ye judta hai</div>
          <pre className="payload">{data.active_prompt}</pre>
        </div>
      )}
    </div>
  );
}
