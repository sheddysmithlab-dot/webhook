import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api, isAdminAuthError } from "../api.js";
import "../wa.css";

const SETTINGS = [
  { id: "callback", label: "Callback URL" },
  { id: "verify", label: "Verify token" },
  { id: "app_id", label: "App ID" },
  { id: "app_secret", label: "App secret" },
  { id: "waba", label: "WhatsApp account (WABA ID)" },
  { id: "phone", label: "Phone number ID" },
  { id: "token", label: "System user token" },
  { id: "graph", label: "Graph API version" },
  { id: "test_recipient", label: "Test recipient" },
  { id: "fields", label: "Webhook fields" },
  { id: "ai", label: "AI setup" },
  { id: "status", label: "Connection & tests" },
  { id: "broadcast", label: "Broadcast message" },
  { id: "simulate", label: "Simulate inbound" },
  { id: "logout", label: "Log out" },
];

const AI_BASES = [
  { label: "OpenAI", url: "https://api.openai.com/v1" },
  { label: "Z.AI (GLM)", url: "https://api.z.ai/api/paas/v4" },
  { label: "Groq", url: "https://api.groq.com/openai/v1" },
  { label: "OpenRouter", url: "https://openrouter.ai/api/v1" },
];

const AI_LANGS = [
  { id: "auto", label: "Auto-detect (customer ki language)" },
  { id: "hinglish", label: "Hinglish" },
  { id: "hi", label: "Hindi" },
  { id: "en", label: "English" },
  { id: "pa", label: "Punjabi" },
  { id: "gu", label: "Gujarati" },
  { id: "mr", label: "Marathi" },
  { id: "ta", label: "Tamil" },
  { id: "te", label: "Telugu" },
  { id: "kn", label: "Kannada" },
  { id: "ml", label: "Malayalam" },
  { id: "bn", label: "Bengali" },
  { id: "ur", label: "Urdu" },
];

const AV_COLORS = ["#6a4c93", "#00a884", "#e67e22", "#2980b9", "#c0392b", "#16a085", "#8e44ad", "#2c3e50"];

function peerOf(c) {
  return c.direction === "outbound" ? c.to : c.from;
}

function initials(name, mobile) {
  const src = (name || mobile || "?").trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
}

function avColor(mobile) {
  const n = Number(String(mobile || "").replace(/\D/g, "").slice(-4) || 0);
  return AV_COLORS[n % AV_COLORS.length];
}

function clock(ts, iso) {
  const d = ts ? new Date(ts) : iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

function isMediaPlaceholder(body) {
  return /^\[(photo|video|media|document|voice note)\]$/i.test(String(body || "").trim());
}

function previewText(m) {
  if (!m) return " ";
  if (m.media_kind === "image" || m.media_url || /^\[photo\]$/i.test(String(m.body || "").trim())) return "📷 Photo";
  if (m.media_kind === "video" || /^\[video\]$/i.test(String(m.body || "").trim())) return "🎥 Video";
  if (isMediaPlaceholder(m.body)) return "📎 Media";
  return m.body || " ";
}

function BubbleBody({ m }) {
  const placeholder = isMediaPlaceholder(m.body);
  const url = m.media_url;
  const kind = m.media_kind || "";
  if (url && (kind === "image" || (!kind && /image\//i.test(m.media_mime || "")))) {
    return (
      <>
        <img className="wa-photo" src={url} alt="" />
        {m.body && !placeholder ? <div>{m.body}</div> : null}
      </>
    );
  }
  if (url && kind === "video") {
    return <video className="wa-photo" src={url} controls playsInline />;
  }
  if (url) {
    return (
      <a className="wa-file" href={url} target="_blank" rel="noreferrer">
        {placeholder ? "📎 File" : m.body}
      </a>
    );
  }
  if (placeholder) return kind === "video" ? "🎥 Video" : "📷 Photo";
  return m.body;
}

function Icon({ d, title }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {title ? <title>{title}</title> : null}
      <path d={d} />
    </svg>
  );
}

function Modal({ title, onClose, children }) {
  return (
    <div className="wa-modal" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="wa-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="wa-modal-h">
          <span>{title}</span>
          <button type="button" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="wa-modal-b">{children}</div>
      </div>
    </div>
  );
}

function StatusDot({ ok }) {
  return <span style={{ color: ok ? "#00a884" : "#e74c3c", fontWeight: 600 }}>{ok ? "Configured" : "Missing"}</span>;
}

export default function Webhook() {
  const [s, setS] = useState(null);
  const [chats, setChats] = useState([]);
  const [blocked, setBlocked] = useState([]);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [openPeer, setOpenPeer] = useState(null);
  const [draft, setDraft] = useState("");
  const [menu, setMenu] = useState(false);
  const [modal, setModal] = useState(null);
  const [flash, setFlash] = useState("");
  const [err, setErr] = useState("");
  const [inbound, setInbound] = useState({ from_mobile: "", name: "", text: "" });
  const [bcMsg, setBcMsg] = useState("");
  const [selected, setSelected] = useState([]);
  const [aiKey, setAiKey] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const threadRef = useRef(null);
  const menuRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const lastOpenPeerRef = useRef(null);

  function scrollThread(force = false) {
    const el = threadRef.current;
    if (!el) return;
    if (force || stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }

  useEffect(() => {
    document.body.classList.add("wa-mode");
    return () => document.body.classList.remove("wa-mode");
  }, []);

  async function loadSettings() {
    setS(await api.settings());
  }
  async function loadChats() {
    const list = await api.chats({ q });
    setChats(list);
    return list;
  }
  async function loadBlocked() {
    const list = await api.admin("blocked");
    setBlocked(list);
    return list;
  }
  function loadAll() {
    setErr("");
    return Promise.all([loadSettings(), loadChats(), loadBlocked()]).catch((e) => {
      setErr(e.message);
      throw e;
    });
  }

  useEffect(() => {
    loadAll().catch(() => {});
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      loadChats().catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, [q]);

  useEffect(() => {
    function close(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  useEffect(() => {
    const el = threadRef.current;
    if (!el || !openPeer) return;
    const onScroll = () => {
      stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [openPeer]);

  useEffect(() => {
    if (openPeer !== lastOpenPeerRef.current) {
      lastOpenPeerRef.current = openPeer;
      stickToBottomRef.current = true;
    }
  }, [openPeer]);

  const convs = useMemo(() => {
    const map = {};
    chats.forEach((c) => {
      const peer = peerOf(c);
      if (!peer) return;
      if (!map[peer]) map[peer] = { peer, name: "", messages: [], unread: 0 };
      map[peer].messages.push(c);
      if (c.direction === "inbound" && c.from_name) map[peer].name = c.from_name;
      if (c.unread && c.direction === "inbound") map[peer].unread += 1;
    });
    return Object.values(map)
      .map((g) => {
        g.messages.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0) || a.id - b.id);
        g.last = g.messages[g.messages.length - 1];
        return g;
      })
      .sort((a, b) => (b.last?.timestamp || 0) - (a.last?.timestamp || 0));
  }, [chats]);

  const active = useMemo(
    () => convs.find((g) => g.peer === openPeer) || null,
    [convs, openPeer],
  );

  const activeMessagesKey = useMemo(() => {
    if (!active) return "";
    const msgs = active.messages;
    const last = msgs[msgs.length - 1];
    return `${msgs.length}:${last?.id || ""}:${last?.body || ""}:${last?.timestamp || ""}`;
  }, [active]);

  useEffect(() => {
    if (!openPeer || !activeMessagesKey) return;
    requestAnimationFrame(() => scrollThread());
  }, [openPeer, activeMessagesKey]);

  const visible = convs.filter((g) => {
    if (filter === "unread" && !g.unread) return false;
    if (!q) return true;
    const hay = `${g.name} ${g.peer} ${g.last?.body || ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  const recipients = convs.map((g) => ({ from: g.peer, name: g.name, last: g.last?.body || "" }));
  const activeBlocked = !!active && blocked.some((b) => String(b.mobile) === String(active.peer));

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
      setFlash("Settings save ho gayi.");
    } catch (e) {
      setFlash(e.message);
    }
  }

  async function openConv(peer) {
    stickToBottomRef.current = true;
    setOpenPeer(peer);
    const g = convs.find((x) => x.peer === peer);
    if (!g) return;
    await Promise.all(
      g.messages.filter((m) => m.unread && m.direction === "inbound").map((m) => api.markRead(m.id).catch(() => {})),
    );
    loadChats().catch(() => {});
  }

  async function sendDraft() {
    const text = draft.trim();
    if (!text || !openPeer) return;
    setDraft("");
    try {
      await api.sendChat({ to: openPeer, text });
      stickToBottomRef.current = true;
      await loadChats();
      requestAnimationFrame(() => scrollThread(true));
    } catch (e) {
      setFlash(e.message);
      setModal("status");
    }
  }

  async function handleDeleteMessage(msg) {
    if (!window.confirm("Is message ko thread se delete karna hai?")) return;
    try {
      const list = await api.deleteChat(msg.id).then(() => loadChats());
      if (!list.some((row) => peerOf(row) === openPeer)) setOpenPeer(null);
    } catch (e) {
      setFlash(e.message);
      setModal("status");
    }
  }

  async function handleDeleteForEveryone(msg) {
    if (msg.direction !== "outbound") {
      setFlash("Delete for everyone sirf sent message par chalega.");
      setModal("status");
      return;
    }
    if (!window.confirm("Is message ko recipient side se bhi delete karna hai?")) return;
    try {
      await api.deleteForEveryone(msg.id);
      await loadChats();
      setFlash("Delete for everyone request bhej di gayi.");
    } catch (e) {
      setFlash(e.message);
      setModal("status");
    }
  }

  async function handleClearChat() {
    if (!active) return;
    if (!window.confirm(`+91 ${active.peer} ki puri chat clear karni hai?`)) return;
    try {
      await api.clearThread(active.peer);
      setOpenPeer(null);
      await Promise.all([loadChats(), loadBlocked()]);
    } catch (e) {
      setFlash(e.message);
      setModal("status");
    }
  }

  async function handleBlockToggle() {
    if (!active) return;
    const label = activeBlocked ? "unblock" : "block";
    if (!window.confirm(`+91 ${active.peer} ko ${label} karna hai?`)) return;
    try {
      if (activeBlocked) {
        await api.unblock(active.peer);
      } else {
        await api.block(active.peer);
      }
      await loadBlocked();
    } catch (e) {
      setFlash(e.message);
      setModal("status");
    }
  }

  function openSetting(id) {
    setMenu(false);
    if (id === "logout") {
      api.logout().finally(() => {
        window.location.href = "/login";
      });
      return;
    }
    setFlash("");
    if (id === "ai") setAiKey("");
    setModal(id);
  }

  async function saveAi(thenTest = false) {
    setAiBusy(true);
    try {
      const saved = await api.saveAiSettings({
        ai_enabled: !!s.ai_enabled,
        ai_api_base: s.ai_api_base || "https://api.openai.com/v1",
        ai_model: s.ai_model || "gpt-4o-mini",
        ai_reply_language: s.ai_reply_language || "auto",
        ai_api_key: aiKey,
      });
      setS(saved);
      setAiKey("");
      if (thenTest) {
        const r = await api.testAi();
        setFlash(`Connected · ${r.model}`);
      } else {
        setFlash("AI setup save ho gaya. Window band karo — WhatsApp pe AI background me chalta rahega.");
      }
    } catch (e) {
      setFlash(e.message);
    } finally {
      setAiBusy(false);
    }
  }

  if (!s) {
    if (err && isAdminAuthError(err)) {
      return <Navigate to="/login" replace />;
    }
    return <div className="wa-unlock">{err || "WhatsApp load ho raha hai…"}</div>;
  }

  return (
    <div className={`wa-app${openPeer ? " thread-open" : ""}`}>
      <aside className="wa-rail">
        <div className="wa-rail-top">
          <Link className="wa-ico on" to="/meta" title="Chats">
            <Icon d="M12 3C6.5 3 2 6.6 2 11c0 2.4 1.3 4.5 3.4 6L4 21l4.2-1.3C9.4 20.2 10.7 20.5 12 20.5 17.5 20.5 22 16.9 22 12.5S17.5 3 12 3z" />
          </Link>
          <Link className="wa-ico" to="/" title="Listings">
            <Icon d="M12 2a10 10 0 100 20 10 10 0 000-20zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
          </Link>
          <Link className="wa-ico" to="/admin" title="Admin">
            <Icon d="M16 11c1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3 1.3 3 3 3zM8 11c1.7 0 3-1.3 3-3S9.7 5 8 5 5 6.3 5 8s1.3 3 3 3zm0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13zm8 0c-.3 0-.6 0-1 .1 1.2.9 2 2 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5z" />
          </Link>
        </div>
        <div className="wa-rail-bot" ref={menuRef}>
          <div className="wa-settings-wrap">
            <button className={`wa-ico${menu ? " on" : ""}`} type="button" title="Settings" onClick={() => setMenu((v) => !v)}>
              <Icon d="M19.1 12.9a7.5 7.5 0 000-1.8l2-1.5-2-3.5-2.4 1a7.6 7.6 0 00-1.5-.9L15 3h-4l-.3 2.7a7.6 7.6 0 00-1.5.9l-2.4-1-2 3.5 2 1.5a7.5 7.5 0 000 1.8l-2 1.5 2 3.5 2.4-1c.5.3 1 .7 1.5.9L11 21h4l.3-2.7c.5-.2 1-.5 1.5-.9l2.4 1 2-3.5-2-1.5zM13 15.5A3.5 3.5 0 1116.5 12 3.5 3.5 0 0113 15.5z" />
            </button>
            {menu && (
              <div className="wa-dd" role="menu">
                {SETTINGS.map((item) => (
                  <button key={item.id} type="button" role="menuitem" onClick={() => openSetting(item.id)}>
                    {item.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </aside>

      <section className="wa-list">
        <div className="wa-list-head">
          <h1>WhatsApp</h1>
          <button className="wa-ico" type="button" title="New chat" style={{ color: "#54656f" }} onClick={() => openSetting("simulate")}>
            <Icon d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z" />
          </button>
        </div>
        <div className="wa-search">
          <span className="lens">⌕</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search or start a new chat" />
        </div>
        <div className="wa-filters">
          <button className={`wa-chip${filter === "all" ? " on" : ""}`} type="button" onClick={() => setFilter("all")}>All</button>
          <button className={`wa-chip${filter === "unread" ? " on" : ""}`} type="button" onClick={() => setFilter("unread")}>Unread</button>
        </div>
        <div className="wa-convs">
          {!visible.length && <div style={{ padding: 24, color: "#667781", fontSize: 14 }}>No chats yet. Customer 8224000826 pe message kare.</div>}
          {visible.map((g) => (
            <button key={g.peer} type="button" className={`wa-conv${openPeer === g.peer ? " on" : ""}`} onClick={() => openConv(g.peer)}>
              <span className="wa-av" style={{ background: avColor(g.peer) }}>{initials(g.name, g.peer)}</span>
              <span className="wa-conv-body">
                <span className="wa-conv-top">
                  <span className="wa-name">{g.name || `+91 ${g.peer}`}</span>
                  <span className="wa-time">{clock(g.last?.timestamp)}</span>
                </span>
                <span className="wa-prev">
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{previewText(g.last)}</span>
                  {g.unread > 0 && <span className="wa-unread">{g.unread}</span>}
                </span>
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="wa-pane">
        {!active && (
          <div className="wa-empty">
            <h2>WhatsApp Web</h2>
            <p>InfraDealer Cloud API inbox. Left se chat kholo, settings icon se Meta fields pop-up me edit karo.</p>
          </div>
        )}
        {active && (
          <>
            <div className="wa-pane-head">
              <div className="wa-pane-who">
                <button type="button" className="wa-ico wa-back" style={{ color: "#54656f" }} onClick={() => setOpenPeer(null)} aria-label="Back">‹</button>
                <span className="wa-av" style={{ background: avColor(active.peer), width: 40, height: 40, fontSize: 15 }}>{initials(active.name, active.peer)}</span>
                <span className="meta">
                  <div className="nm">{active.name || `+91 ${active.peer}`}</div>
                  <div className="st">+91 {active.peer} · click to chat</div>
                </span>
              </div>
              <div className="wa-pane-actions">
                <button type="button" className={`wa-mini${activeBlocked ? "" : " warn"}`} onClick={handleBlockToggle}>
                  {activeBlocked ? "Unblock" : "Block chat"}
                </button>
                <button type="button" className="wa-mini danger" onClick={handleClearChat}>Clear chat</button>
              </div>
            </div>
            <div className="wa-thread" ref={threadRef}>
              {active.messages.map((m) => (
                <div key={m.id} className={`wa-bub ${m.direction === "outbound" ? "out" : "in"}`}>
                  <div className="wa-msg-actions">
                    {m.direction === "outbound" && (
                      <button
                        type="button"
                        className="wa-msg-del"
                        onClick={() => handleDeleteForEveryone(m)}
                        aria-label="Delete for everyone"
                        title="Delete for everyone"
                      >
                        ↻
                      </button>
                    )}
                    <button type="button" className="wa-msg-del" onClick={() => handleDeleteMessage(m)} aria-label="Delete message" title="Delete for me">
                      ×
                    </button>
                  </div>
                  <BubbleBody m={m} />
                  <div className="meta">
                    <span>{clock(m.timestamp)}</span>
                    {m.direction === "outbound" && <span className="wa-ticks">{m.status === "read" || m.status === "delivered" ? "✓✓" : "✓"}</span>}
                  </div>
                </div>
              ))}
            </div>
            <form className="wa-composer" onSubmit={(e) => { e.preventDefault(); sendDraft(); }}>
              <textarea
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Type a message"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendDraft();
                  }
                }}
              />
              <button className="wa-send" type="submit" disabled={!draft.trim()} aria-label="Send">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z" /></svg>
              </button>
            </form>
          </>
        )}
      </section>

      {modal === "callback" && (
        <Modal title="Callback URL" onClose={() => setModal(null)}>
          <label>Callback URL</label>
          <input readOnly value={s.callback_url} />
          <p className="hint">Meta → WhatsApp → Configuration → Webhook me paste karo.</p>
        </Modal>
      )}
      {modal === "verify" && (
        <Modal title="Verify token" onClose={() => setModal(null)}>
          <label>Verify token</label>
          <input readOnly value={s.verify_token} />
          <div className="row">
            <button className="btn primary" type="button" onClick={async () => { setS(await api.regenToken()); setFlash("Naya verify token ban gaya."); }}>Regenerate</button>
          </div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "app_id" && (
        <Modal title="App ID" onClose={() => setModal(null)}>
          <label>App ID</label>
          <input value={s.app_id} onChange={(e) => field("app_id", e.target.value)} />
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "app_secret" && (
        <Modal title="App secret" onClose={() => setModal(null)}>
          <label>App secret</label>
          <input type="password" value={s.app_secret} onChange={(e) => field("app_secret", e.target.value)} autoComplete="off" />
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "waba" && (
        <Modal title="WhatsApp account (WABA ID)" onClose={() => setModal(null)}>
          <label>WABA ID</label>
          <input value={s.waba_id} onChange={(e) => field("waba_id", e.target.value)} />
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "phone" && (
        <Modal title="Phone number ID" onClose={() => setModal(null)}>
          <label>Phone number ID</label>
          <input value={s.phone_number_id} onChange={(e) => field("phone_number_id", e.target.value)} />
          <p className="hint">From number: +91 82240 00826</p>
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "token" && (
        <Modal title="System user token" onClose={() => setModal(null)}>
          <label>Access token</label>
          <input type="password" value={s.system_user_token} onChange={(e) => field("system_user_token", e.target.value)} autoComplete="off" />
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "graph" && (
        <Modal title="Graph API version" onClose={() => setModal(null)}>
          <label>Version</label>
          <select value={s.graph_version} onChange={(e) => field("graph_version", e.target.value)}>
            <option>v23.0</option><option>v22.0</option><option>v21.0</option>
          </select>
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "test_recipient" && (
        <Modal title="Test recipient" onClose={() => setModal(null)}>
          <label>Personal WhatsApp number</label>
          <input inputMode="numeric" value={s.test_recipient} onChange={(e) => field("test_recipient", e.target.value)} />
          <div className="row">
            <button className="btn primary" type="button" onClick={save}>Save</button>
            <button className="btn" type="button" onClick={async () => {
              try {
                const r = await api.testMessage();
                setFlash(`Sent · ${r.wamid || "ok"}`);
                loadChats();
              } catch (e) { setFlash(e.message); }
            }}>Send test message</button>
          </div>
          {flash && <div className={flash.startsWith("Sent") || flash.startsWith("Settings") ? "wa-ok" : "wa-bad"}>{flash}</div>}
        </Modal>
      )}
      {modal === "fields" && (
        <Modal title="Webhook fields" onClose={() => setModal(null)}>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input className="wa-check" type="checkbox" checked={s.webhook_fields.messages} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, messages: e.target.checked } })} /> messages
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <input className="wa-check" type="checkbox" checked={s.webhook_fields.message_template_status_update} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, message_template_status_update: e.target.checked } })} /> message_template_status_update
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <input className="wa-check" type="checkbox" checked={s.webhook_fields.account_alerts} onChange={(e) => setS({ ...s, webhook_fields: { ...s.webhook_fields, account_alerts: e.target.checked } })} /> account_alerts
          </label>
          <div className="row"><button className="btn primary" type="button" onClick={save}>Save</button></div>
          {flash && <div className="wa-ok">{flash}</div>}
        </Modal>
      )}
      {modal === "ai" && (
        <Modal title="AI setup" onClose={() => setModal(null)}>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="wa-check"
              type="checkbox"
              checked={!!s.ai_enabled}
              onChange={(e) => field("ai_enabled", e.target.checked)}
            />
            WhatsApp AI agent on
          </label>
          <label>Reply language</label>
          <select
            value={s.ai_reply_language || "auto"}
            onChange={(e) => field("ai_reply_language", e.target.value)}
          >
            {AI_LANGS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <label>Provider</label>
          <select
            value={AI_BASES.some((p) => p.url === (s.ai_api_base || "").replace(/\/$/, "")) ? (s.ai_api_base || "").replace(/\/$/, "") : "custom"}
            onChange={(e) => {
              if (e.target.value === "custom") return;
              field("ai_api_base", e.target.value);
            }}
          >
            {AI_BASES.map((p) => (
              <option key={p.url} value={p.url}>{p.label}</option>
            ))}
            <option value="custom">Custom</option>
          </select>
          <label>API base URL</label>
          <input
            value={s.ai_api_base || ""}
            onChange={(e) => field("ai_api_base", e.target.value)}
            placeholder="https://api.openai.com/v1"
            autoComplete="off"
          />
          <label>Model</label>
          <input
            value={s.ai_model || ""}
            onChange={(e) => field("ai_model", e.target.value)}
            placeholder="gpt-4o-mini"
            autoComplete="off"
          />
          <label>API key</label>
          <input
            type="password"
            value={aiKey}
            onChange={(e) => setAiKey(e.target.value)}
            placeholder={s.ai_api_key_set ? "Nayi key paste karo — khali chhodo to pehli key rahegi" : "sk-... paste karo"}
            autoComplete="new-password"
          />
          <p className="hint">
            {s.ai_api_key_set
              ? `Key server pe save hai (${s.ai_api_key_hint || "hidden"}). Save ke baad window band karo — inbound WhatsApp pe AI chalta rahega. Full key browser me nahi aati.`
              : "Key database me permanent save hoti hai. Window band hone ke baad bhi Python AI engine WhatsApp messages pe chalega. Listing khud LIVE nahi hoti."}
            {" "}
            Auto-detect: customer Hindi/Punjabi/Tamil/English etc. me likhe to agent usi language me reply karega. Force language select karoge to har chat usi me rahegi.
          </p>
          <div className="row">
            <button className="btn primary" type="button" disabled={aiBusy} onClick={() => saveAi(false)}>
              {aiBusy ? "Saving…" : "Save"}
            </button>
            <button className="btn" type="button" disabled={aiBusy} onClick={() => saveAi(true)}>
              Test connection
            </button>
          </div>
          {flash && <div className={/save|Connected/i.test(flash) ? "wa-ok" : "wa-bad"}>{flash}</div>}
        </Modal>
      )}
      {modal === "status" && (
        <Modal title="Connection & tests" onClose={() => setModal(null)}>
          <div className="st"><span>Callback URL</span><StatusDot ok={s.configured.callback_url} /></div>
          <div className="st"><span>Verify token</span><StatusDot ok={s.configured.verify_token} /></div>
          <div className="st"><span>App secret</span><StatusDot ok={s.configured.app_secret} /></div>
          <div className="st"><span>WABA ID</span><StatusDot ok={s.configured.waba_id} /></div>
          <div className="st"><span>Phone number ID</span><StatusDot ok={s.configured.phone_number_id} /></div>
          <div className="st"><span>System user token</span><StatusDot ok={s.configured.system_user_token} /></div>
          <div className="st"><span>AI API key</span><StatusDot ok={s.configured.ai_api_key} /></div>
          <div className="st"><span>Subscribe</span><span>{s.subscribed ? "Subscribed" : "Not subscribed"}</span></div>
          <p className="hint">Last delivery: {s.last_delivery || "—"}</p>
          <div className="row">
            <button className="btn" type="button" onClick={async () => {
              const url = `/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=${encodeURIComponent(s.verify_token)}&hub.challenge=challenge_local`;
              const res = await fetch(url);
              setFlash(`Verify ${res.status}: ${await res.text()}`);
            }}>Test verify</button>
            <button className="btn" type="button" onClick={async () => {
              try { setFlash(JSON.stringify(await api.subscribe())); loadSettings(); } catch (e) { setFlash(e.message); }
            }}>Subscribe</button>
            <button className="btn" type="button" onClick={async () => {
              try { await api.toAdmin(); setFlash("Admin ledger update ho gaya."); } catch (e) { setFlash(e.message); }
            }}>Push to admin</button>
          </div>
          {flash && <div className="wa-ok" style={{ whiteSpace: "pre-wrap" }}>{flash}</div>}
        </Modal>
      )}
      {modal === "broadcast" && (
        <Modal title="Broadcast message" onClose={() => setModal(null)}>
          <p className="hint">{selected.length} selected</p>
          <div className="row">
            <button className="btn small" type="button" onClick={() => setSelected(recipients.map((r) => r.from))}>Select all</button>
            <button className="btn small" type="button" onClick={() => setSelected([])}>Clear</button>
          </div>
          <div style={{ maxHeight: 180, overflow: "auto", marginTop: 10 }}>
            {recipients.map((r) => (
              <label key={r.from} style={{ display: "flex", gap: 8, padding: "6px 0", fontSize: 14 }}>
                <input className="wa-check" type="checkbox" checked={selected.includes(r.from)} onChange={(e) => setSelected(e.target.checked ? [...selected, r.from] : selected.filter((x) => x !== r.from))} />
                {r.name || r.from} · +91 {r.from}
              </label>
            ))}
          </div>
          <label>Message</label>
          <textarea rows={3} value={bcMsg} onChange={(e) => setBcMsg(e.target.value)} />
          <div className="row">
            <button className="btn primary" type="button" onClick={async () => {
              try {
                const r = await api.broadcast({ message: bcMsg, recipients: selected });
                setFlash(`Broadcast ${r.delivered}/${r.total} delivered.`);
                setBcMsg("");
                loadChats();
              } catch (e) { setFlash(e.message); }
            }}>Send broadcast</button>
          </div>
          {flash && <div className={flash.startsWith("Broadcast") ? "wa-ok" : "wa-bad"}>{flash}</div>}
        </Modal>
      )}
      {modal === "simulate" && (
        <Modal title="Simulate inbound" onClose={() => setModal(null)}>
          <label>From mobile</label>
          <input value={inbound.from_mobile} onChange={(e) => setInbound({ ...inbound, from_mobile: e.target.value })} placeholder="9876543210" />
          <label>Name</label>
          <input value={inbound.name} onChange={(e) => setInbound({ ...inbound, name: e.target.value })} />
          <label>Message</label>
          <textarea rows={3} value={inbound.text} onChange={(e) => setInbound({ ...inbound, text: e.target.value })} />
          <div className="row">
            <button className="btn primary" type="button" onClick={async () => {
              try {
                const r = await api.inbound(inbound);
                setFlash(`Saved · ref ${r.ref}`);
                setInbound({ from_mobile: "", name: "", text: "" });
                loadChats();
              } catch (e) { setFlash(e.message); }
            }}>Save inbound</button>
          </div>
          {flash && <div className={flash.startsWith("Saved") ? "wa-ok" : "wa-bad"}>{flash}</div>}
        </Modal>
      )}
    </div>
  );
}
