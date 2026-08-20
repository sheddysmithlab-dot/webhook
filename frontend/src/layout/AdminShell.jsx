import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api.js";

const NAV = [
  { to: "/admin", label: "Dashboard", icon: "grid", end: true },
  { to: "/", label: "Listings", icon: "list", end: true },
  { to: "/meta", label: "WhatsApp", icon: "chat", end: false },
  { to: "/admin/infradealer", label: "Webhook", icon: "plug", end: false },
];

function Icon({ name }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.8",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };
  if (name === "grid") {
    return (
      <svg {...common}>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    );
  }
  if (name === "list") {
    return (
      <svg {...common}>
        <rect x="3" y="4" width="18" height="6" rx="1" />
        <rect x="3" y="14" width="18" height="6" rx="1" />
      </svg>
    );
  }
  if (name === "chat") {
    return (
      <svg {...common}>
        <path d="M21 12a8 8 0 01-8 8H7l-4 3V12a8 8 0 018-8h2a8 8 0 018 8z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

async function logout() {
  try {
    await api.logout();
  } catch {
    /* ignore */
  }
  window.location.href = "/login";
}

export default function AdminShell({ children, flush = false }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  return (
    <div className={`dash-shell${flush ? " flush" : ""}`}>
      <a className="skip-link" href="#app">Main content par jayein</a>
      <header className="dash-top">
        <button type="button" className="dash-menu" aria-label="Open menu" onClick={() => setOpen(true)}>
          <span />
          <span />
          <span />
        </button>
        <NavLink className="dash-brand" to="/admin">
          <span className="dash-logo" aria-hidden>in</span>
          <span>
            <b>InfraDealer</b>
            <small>Admin Panel</small>
          </span>
        </NavLink>
        <div className="dash-user">
          <div>
            <strong>Administrator</strong>
            <span>Admin User</span>
          </div>
          <button className="dash-logout" type="button" onClick={logout}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
            Logout
          </button>
        </div>
      </header>
      <div className="dash-body">
        {open && <button type="button" className="dash-scrim" aria-label="Close menu" onClick={() => setOpen(false)} />}
        <aside className={`dash-side${open ? " open" : ""}`}>
          <nav>
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => {
                  const extra = item.to === "/" && loc.pathname === "/list";
                  return `dash-nav${isActive || extra ? " on" : ""}`;
                }}
                onClick={() => setOpen(false)}
              >
                <Icon name={item.icon} />
                {item.label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className={`dash-main${flush ? " flush" : ""}`} id="app" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
