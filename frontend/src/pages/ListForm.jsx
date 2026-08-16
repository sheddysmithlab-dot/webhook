import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api.js";

const DEFAULT_CATS = ["Bike", "Car", "Mobile Phone", "Laptop", "Refrigerator", "AC / Cooler", "Furniture", "Washing Machine", "TV / Projector", "Camera", "Watch", "Home Appliance", "Other"];
const CONDITIONS = ["Brand New", "Like New", "Excellent", "Very Good", "Good", "Fair", "Average", "Used", "Refurbished"];

export default function ListForm() {
  const [params] = useSearchParams();
  const ref = params.get("ref") || "";
  const [opts, setOpts] = useState({ categories: DEFAULT_CATS, conditions: CONDITIONS });
  const [msgErr, setMsgErr] = useState("");
  const [messageId, setMessageId] = useState(null);
  const [form, setForm] = useState({
    title: params.get("title") || "",
    category: params.get("cat") || "Other",
    price: params.get("price") || "",
    condition: params.get("cond") || "",
    city: params.get("city") || "",
    mobile: params.get("mobile") || "",
    name: params.get("name") || "",
    description: "",
    consent: false,
  });
  const [err, setErr] = useState("");
  const [otp, setOtp] = useState(null);
  const [otpCode, setOtpCode] = useState("");
  const [otpMsg, setOtpMsg] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    api.options().then(setOpts).catch(() => {});
  }, []);

  useEffect(() => {
    if (!ref) return;
    api.messageByRef(ref)
      .then((m) => {
        setMessageId(m.id);
        const p = m.parsed || {};
        setForm((f) => ({
          ...f,
          title: p.product_name || f.title,
          category: p.category || f.category,
          price: String(p.price_num || p.price || f.price).replace(/[^0-9]/g, ""),
          condition: p.condition || f.condition,
          city: p.city || f.city,
          mobile: p.mobile || m.from || f.mobile,
        }));
      })
      .catch((e) => setMsgErr(e.message));
  }, [ref]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setInterval(() => setCooldown((c) => c - 1), 1000);
    return () => clearInterval(t);
  }, [cooldown]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function onSubmit(e) {
    e.preventDefault();
    const mobile = String(form.mobile).replace(/\D/g, "").slice(-10);
    const errs = [];
    if (!form.title.trim()) errs.push("Product name");
    if (!form.category) errs.push("Category");
    if (!form.price || !(parseFloat(form.price) > 0)) errs.push("Price");
    if (!form.condition) errs.push("Condition");
    if (!form.city.trim()) errs.push("City");
    if (!/^[6-9]\d{9}$/.test(mobile)) errs.push("Valid 10-digit mobile (6-9 se shuru)");
    if (!form.name.trim()) errs.push("Aapka naam");
    if (!form.consent) errs.push("Public card consent");
    if (errs.length) {
      setErr(`Ye fields bhariye: ${errs.join(", ")}`);
      return;
    }
    setErr("");
    setBusy(true);
    try {
      const r = await api.submit({
        ref,
        message_id: messageId,
        title: form.title.trim(),
        category: form.category,
        price: parseFloat(form.price),
        condition: form.condition,
        city: form.city.trim(),
        mobile,
        name: form.name.trim(),
        description: form.description,
        consent: true,
      });
      if (r.need_otp) {
        setOtp({ submission_id: r.submission_id, mobile: r.mobile });
        setOtpMsg(r.message);
      } else {
        setResult(r);
      }
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setErr("");
    try {
      const r = await api.verifyOtp({ submission_id: otp.submission_id, mobile: otp.mobile, code: otpCode });
      setResult(r);
      setOtp(null);
    } catch (ex) {
      setErr(ex.message);
    }
  }

  async function resend() {
    try {
      const r = await api.resendOtp({ submission_id: otp.submission_id, mobile: otp.mobile });
      setOtpMsg(r.otp_channel === "whatsapp" ? "Naya OTP WhatsApp par bheja gaya." : "Naya OTP backend log mein hai.");
      setCooldown(30);
    } catch (ex) {
      setErr(ex.message);
    }
  }

  if (ref && msgErr) {
    return (
      <>
        <div className="label">WhatsApp → Form</div>
        <h1 className="h2" style={{ fontSize: 28 }}>Link Invalid Ya Expire Ho Gaya</h1>
        <div className="panel">
          <div className="err">Ye reference ({ref}) system mein nahi mila. WhatsApp message 48h ke baad expire ho jata hai.</div>
          <p style={{ marginBottom: "var(--space-4)" }}>Form directly bhar kar bhi card bana sakte hain:</p>
          <Link className="btn primary" to="/list">Direct Form Kholen</Link>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="label">WhatsApp → Form · Ref {ref || "—"}</div>
      <h1 className="h2" style={{ fontSize: 28 }}>Product Card Banayein</h1>
      <p className="lede">
        {ref
          ? "Aapke WhatsApp message se ye details auto-detect hui hain. Check karein, jo galat ho use sudhar lein, consent dekar publish karein."
          : "Form bhar kar card publish karein. Unregistered number par WhatsApp OTP lagega."}
      </p>
      <div className="panel" id="formpanel">
        {err && <div className="err" role="alert">{err}</div>}
        {!result && !otp && (
          <form onSubmit={onSubmit}>
            <div className="form-grid">
              <div className="field"><label htmlFor="ff_title">Product Name *</label><input id="ff_title" value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Honda Activa 6G 2021" /></div>
              <div className="field">
                <label htmlFor="ff_cat">Category *</label>
                <select id="ff_cat" value={form.category} onChange={(e) => set("category", e.target.value)}>
                  {opts.categories.map((c) => <option key={c}>{c}</option>)}
                </select>
              </div>
              <div className="field"><label htmlFor="ff_price">Price (₹) *</label><input id="ff_price" type="number" min="1" value={form.price} onChange={(e) => set("price", e.target.value)} placeholder="45000" /></div>
              <div className="field">
                <label htmlFor="ff_cond">Condition *</label>
                <select id="ff_cond" value={form.condition} onChange={(e) => set("condition", e.target.value)}>
                  <option value="">Choose...</option>
                  {opts.conditions.map((c) => <option key={c}>{c}</option>)}
                </select>
              </div>
              <div className="field"><label htmlFor="ff_city">City *</label><input id="ff_city" autoComplete="address-level2" value={form.city} onChange={(e) => set("city", e.target.value)} placeholder="Delhi" /></div>
              <div className="field"><label htmlFor="ff_mobile">Mobile Number *</label><input id="ff_mobile" inputMode="numeric" autoComplete="tel" value={form.mobile} onChange={(e) => set("mobile", e.target.value)} placeholder="10-digit mobile" /></div>
              <div className="field"><label htmlFor="ff_name">Aapka Naam *</label><input id="ff_name" autoComplete="name" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Card par dikhega" /></div>
              <div className="field full"><label htmlFor="ff_desc">Description (optional)</label><textarea id="ff_desc" rows="3" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Kuch extra details..." /></div>
            </div>
            <label className="consent">
              <input type="checkbox" checked={form.consent} onChange={(e) => set("consent", e.target.checked)} />
              <div className="txt"><b>Public Card Consent *</b>Main samajhta hoon ki mera naam aur WhatsApp number public product card par dikhega, taaki khareedaar mujhse seedha baat kar sake.</div>
            </label>
            <button className="btn primary" type="submit" disabled={busy}>Card Publish Karein</button>
          </form>
        )}
        {otp && !result && (
          <div>
            <div className="otp-demo">
              OTP <span className="mono">+91 {otp.mobile}</span> par bheja gaya
              <div style={{ fontSize: 13, marginTop: 8, color: "#bbb" }}>{otpMsg}</div>
              <div style={{ fontSize: 12, color: "#bbb", marginTop: 6 }}>5 minute expire · 3 attempts · max 3 OTP/15 min · code screen par nahi dikhta</div>
            </div>
            <div className="form-grid" style={{ marginBottom: "var(--space-4)" }}>
              <div className="field"><label htmlFor="ff_otp">OTP Code *</label><input id="ff_otp" inputMode="numeric" maxLength={6} value={otpCode} onChange={(e) => setOtpCode(e.target.value)} placeholder="6-digit OTP" /></div>
            </div>
            <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", flexWrap: "wrap" }}>
              <button className="btn" type="button" onClick={verify}>OTP Verify Karein</button>
              <button className="btn small" type="button" disabled={cooldown > 0} onClick={resend}>Resend OTP</button>
              {cooldown > 0 && <span style={{ fontSize: 12, color: "var(--gray)" }}>Resend: {cooldown}s</span>}
            </div>
          </div>
        )}
        {result && result.product && (
          <div>
            <div className="okbox">
              <b>Card publish ho gaya!</b><br />
              {result.product.status === "draft" ? "Card draft mode mein hai." : "Card public listing mein live ho gaya."}<br />
              {result.account_mode === "created" ? "Naya account WhatsApp OTP se auto-create ho gaya." : "Number pehle se registered tha — existing account se link ho gaya."}<br />
              Ref: <b className="mono">{result.product.ref}</b>
            </div>
            <div className="parsed">
              <table>
                <tbody>
                  <tr><td>Title</td><td>{result.product.title}</td></tr>
                  <tr><td>Price</td><td>₹{result.product.price}</td></tr>
                  <tr><td>Seller</td><td>{result.product.seller_name} · +91 {result.product.mobile}</td></tr>
                  <tr><td>Status</td><td>{result.product.status}</td></tr>
                </tbody>
              </table>
              <p style={{ marginTop: 12 }}>
                <Link className="btn small primary" to="/">Listing dekhein →</Link>{" "}
                <Link className="btn small" to="/admin">Admin records</Link>
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
