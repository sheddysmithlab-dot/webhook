import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api.js";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
      setErr("Invalid email or password.");
    } finally {
      setBusy(false);
    }
  }

  if (session === "checking") {
    return <div className="login-page"><div className="login-card"><p>Checking session…</p></div></div>;
  }
  if (session === "ok") {
    return <Navigate to="/meta" replace />;
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark">in</div>
        <h1>InfraDealer</h1>
        <p>Secure login required</p>
        {err && <div className="login-err" role="alert">{err}</div>}
        <label htmlFor="login-email">Email</label>
        <input
          id="login-email"
          name="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="username"
          spellCheck={false}
        />
        <label htmlFor="login-pass">Password</label>
        <input
          id="login-pass"
          name="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
        />
        <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Log in"}</button>
      </form>
    </div>
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
    return <div className="login-page"><div className="login-card"><p>Checking session…</p></div></div>;
  }
  if (state !== "ok") {
    return <Navigate to="/login" replace />;
  }
  return children;
}
