async function request(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  const res = await fetch(path, { ...options, headers });
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
  testMessage: () => request("/api/meta/test-message", { method: "POST", body: JSON.stringify({}) }),
  subscribe: () => request("/api/meta/subscribe", { method: "POST" }),
  broadcast: (body) => request("/api/broadcast", { method: "POST", body: JSON.stringify(body) }),
  exportUrl: (kind) => `/api/admin/export/${kind}`,
};

export function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso || "—";
  }
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
