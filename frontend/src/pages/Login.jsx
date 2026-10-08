import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api.js";

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

function BrandMark() {
  return (
    <div className="login-mark" aria-hidden>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <path d="M12 3l7.5 4.3v9.4L12 21l-7.5-4.3V7.3L12 3z" stroke="#fff" strokeWidth="1.6" />
        <path d="M12 8.2v7.6M8.8 10.2 12 12l3.2-1.8" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

function LoginShell({ children, className = "" }) {
  return (
    <div className={`login-page ${className}`.trim()}>
      <div className="login-bg" aria-hidden>
        <div className="login-bg-glow" />
        <div className="login-bg-grid" />
      </div>
      {children}
    </div>
  );
}

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState("checking");

  useEffect(() => {
    let live = true;
    api.me()
      .then(() => { if (live) setSession("ok"); })
      .catch(() => { if (live) setSession("no"); });
    return () => { live = false; };
  }, []);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await api.login(email, password);
      setPassword("");
      nav("/meta", { replace: true });
    } catch {
      setPassword("");
      setErr("Invalid email or password. Check details and try again.");
    } finally {
      setBusy(false);
    }
  }

  if (session === "checking") {
    return (
      <LoginShell className="login-page--loading">
        <div className="login-panel login-panel--loading" role="status">
          <BrandMark />
          <p className="login-loading-text">Checking session…</p>
        </div>
      </LoginShell>
    );
  }
  if (session === "ok") {
    return <Navigate to="/meta" replace />;
  }

  return (
    <LoginShell>
      <form className="login-panel" onSubmit={submit} noValidate>
        <header className="login-brand">
          <BrandMark />
          <h1>InfraDealer</h1>
          <p>WhatsApp admin console — secure sign in</p>
        </header>

        {err && (
          <div className="login-err" role="alert">
            {err}
          </div>
        )}

        <div className="login-fields">
          <div className="login-field">
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              name="email"
              type="email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); if (err) setErr(""); }}
              required
              autoComplete="username"
              spellCheck={false}
              placeholder="you@company.com"
              autoFocus
            />
          </div>

          <div className="login-field">
            <label htmlFor="login-pass">Password</label>
            <div className="login-pass-wrap">
              <input
                id="login-pass"
                name="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => { setPassword(e.target.value); if (err) setErr(""); }}
                required
                autoComplete="current-password"
                placeholder="Enter your password"
              />
              <button
                type="button"
                className="login-eye"
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                onClick={() => setShowPassword((v) => !v)}
              >
                <EyeIcon open={showPassword} />
              </button>
            </div>
          </div>
        </div>

        <button type="submit" className="login-submit" disabled={busy || !email.trim() || !password}>
          {busy ? (
            <span className="login-submit-busy">
              <span className="login-spinner" aria-hidden />
              Signing in…
            </span>
          ) : (
            "Log in"
          )}
        </button>

        <p className="login-foot">Authorized staff only · MALWA TROLLEY</p>
      </form>
    </LoginShell>
  );
}

export function RequireAuth({ children }) {
  const [state, setState] = useState("loading");

  useEffect(() => {
    let live = true;
    api.me()
      .then(() => { if (live) setState("ok"); })
      .catch(() => { if (live) setState("no"); });
    return () => { live = false; };
  }, []);

  if (state === "loading") {
    return (
      <LoginShell className="login-page--loading">
        <div className="login-panel login-panel--loading" role="status">
          <BrandMark />
          <p className="login-loading-text">Checking session…</p>
        </div>
      </LoginShell>
    );
  }
  if (state !== "ok") {
    return <Navigate to="/login" replace />;
  }
  return children;
}
