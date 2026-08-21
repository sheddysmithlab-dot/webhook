const TOKEN_KEY = "idw_admin_token";

export function getAdminToken() {
  return "";
}

export function setAdminToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function isAdminAuthError(err) {
  const msg = err && err.message ? err.message : String(err || "");
  return /login required|invalid email|401/i.test(msg);
}

async function request(path, options = {}) {
  const headers = {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  const res = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : text || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const api = {
  me: () => request("/api/auth/me"),
  login: (email, password) => request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  health: () => request("/api/health"),
  options: () => request("/api/meta/options"),
  products: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== "" && v != null) q.set(k, v);
    });
    return request(`/api/products?${q.toString()}`);
  },
  product: (id) => request(`/api/products/${id}`),
  messageByRef: (ref) => request(`/api/messages/by-ref/${encodeURIComponent(ref)}`),
  submit: (body) => request("/api/submissions", { method: "POST", body: JSON.stringify(body) }),
  verifyOtp: (body) => request("/api/otp/verify", { method: "POST", body: JSON.stringify(body) }),
  resendOtp: (body) => request("/api/otp/resend", { method: "POST", body: JSON.stringify(body) }),
  stats: () => request("/api/admin/stats"),
  admin: (kind) => request(`/api/admin/${kind}`),
  deleteSubmission: (id) => request(`/api/admin/submissions/${id}`, { method: "DELETE" }),
  toggleProduct: (id) => request(`/api/admin/products/${id}/toggle`, { method: "POST" }),
  deleteProduct: (id) => request(`/api/admin/products/${id}`, { method: "DELETE" }),
  block: (mobile) => request("/api/admin/blocked", { method: "POST", body: JSON.stringify({ mobile }) }),
  unblock: (mobile) => request(`/api/admin/blocked/${mobile}`, { method: "DELETE" }),
  settings: () => request("/api/admin/settings"),
  saveSettings: (body) => request("/api/admin/settings", { method: "PUT", body: JSON.stringify(body) }),
  saveAiSettings: (body) => request("/api/admin/settings/ai", { method: "PUT", body: JSON.stringify(body) }),
  testAi: () => request("/api/admin/settings/ai/test", { method: "POST" }),
  regenToken: () => request("/api/admin/settings/regenerate-token", { method: "POST" }),
  chats: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== "" && v != null && v !== false) q.set(k, v);
    });
    return request(`/api/chats?${q.toString()}`);
  },
  toAdmin: () => request("/api/chats/to-admin", { method: "POST" }),
  inbound: (body) => request("/api/chats/inbound", { method: "POST", body: JSON.stringify(body) }),
  sendChat: (body) => request("/api/chats/send", { method: "POST", body: JSON.stringify(body) }),
  markRead: (id) => request(`/api/chats/${id}/read`, { method: "POST" }),
  deleteChat: (id) => request(`/api/chats/${id}`, { method: "DELETE" }),
  deleteForEveryone: (id) => request(`/api/chats/${id}/delete-for-everyone`, { method: "POST" }),
  clearThread: (mobile) => request(`/api/chats/thread/${mobile}`, { method: "DELETE" }),
  testMessage: (text) => request("/api/meta/test-message", { method: "POST", body: JSON.stringify(text ? { text } : {}) }),
  subscribe: () => request("/api/meta/subscribe", { method: "POST" }),
  broadcast: (body) => request("/api/broadcast", { method: "POST", body: JSON.stringify(body) }),
  aiDrafts: () => request("/api/admin/ai/drafts"),
  aiDraft: (id) => request(`/api/admin/ai/drafts/${id}`),
  aiDraftStatus: (id, status, note = "") =>
    request(`/api/admin/ai/drafts/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status, note: note || "" }),
    }),
  aiDraftResendDecision: (id, note = "") =>
    request(`/api/admin/ai/drafts/${id}/resend-decision`, {
      method: "POST",
      body: JSON.stringify({ note: note || "" }),
    }),
  aiDraftPost: (id) => request(`/api/admin/ai/drafts/${id}/post`, { method: "POST" }),
  aiMediaUrl: (id) => `/api/admin/ai/media/${id}`,
  exportUrl: (kind) => `/api/admin/export/${kind}`,
  infraConfig: () => request("/api/admin/infradealer"),
  infraSave: (body) => request("/api/admin/infradealer", { method: "PUT", body: JSON.stringify(body) }),
  infraTest: () => request("/api/admin/infradealer/test", { method: "POST" }),
  infraDisconnect: () => request("/api/admin/infradealer/disconnect", { method: "POST" }),
  infraRegenSecret: () => request("/api/admin/infradealer/regenerate-secret", { method: "POST" }),
  infraEvents: (event_flags) => request("/api/admin/infradealer/events", { method: "PUT", body: JSON.stringify({ event_flags }) }),
  infraLedger: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== "" && v != null && v !== false) q.set(k, v);
    });
    return request(`/api/admin/infradealer/ledger?${q.toString()}`);
  },
  infraLedgerDetail: (requestId) => request(`/api/admin/infradealer/ledger/${encodeURIComponent(requestId)}`),
  infraErrors: () => request("/api/admin/infradealer/errors"),
  infraCallbacks: () => request("/api/admin/infradealer/callbacks"),
  infraRetry: (requestId) => request(`/api/admin/infradealer/retry/${encodeURIComponent(requestId)}`, { method: "POST" }),
};

function parseIso(iso) {
  if (!iso) return null;
  const raw = String(iso);
  const withTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`;
  const d = new Date(withTz);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtTime(iso) {
  const d = parseIso(iso);
  if (!d) return iso || "—";
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function fmtTimeLong(iso) {
  const d = parseIso(iso);
  if (!d) return "—";
  return d.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

export function loadFavs() {
  try {
    return JSON.parse(localStorage.getItem("idw_favs") || "[]");
  } catch {
    return [];
  }
}

export function saveFavs(ids) {
  localStorage.setItem("idw_favs", JSON.stringify(ids));
}
