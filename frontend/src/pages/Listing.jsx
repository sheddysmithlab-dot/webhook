import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtTime, loadFavs, saveFavs } from "../api.js";

function pinSvg() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0B3D29" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 1 1 16 0Z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  );
}

function ProductModal({ product, onClose }) {
  useEffect(() => {
    document.body.classList.add("modal-open");
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const waText = encodeURIComponent(`Hi! Aapke ${product.title} ke baare mein baat karni hai (infradealer se).`);
  const qs = new URLSearchParams({
    title: product.title,
    cat: product.category,
    price: String(product.price),
    cond: product.condition,
    city: product.city,
    mobile: product.mobile,
    name: product.seller_name,
  });

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={product.title}>
        <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
        <div className="gallery">
          <span className="cat-badge lg">{product.category}</span>
          <div className="ph-block">{(product.category || "?").charAt(0).toUpperCase()}<span>{product.category} · Photo</span></div>
          <span className="gcount">1/1</span>
        </div>
        <div className="modal-body">
          <div className="m-title">{product.title}</div>
          <div className="m-sub">{product.category} · {product.condition}</div>
          <div className="verified"><span className="v-badge">✓</span> Verified Listing · <span className="mono">{product.ref}</span></div>
          <div className="meta-row">
            <span>{pinSvg()} {product.city}</span>
            <span>Posted {fmtTime(product.created_at)}</span>
            <span>{product.views || 0} views</span>
          </div>
          <div className="pricebox">
            <div className="pb-label">Asking Price</div>
            <div className="pb-price mono">₹{product.price}</div>
            <div className="pb-meta">Last updated: {fmtTime(product.updated_at || product.created_at)}</div>
          </div>
          <div className="pipe">Key Details</div>
          <div className="spec-grid">
            {[
              ["Listing ID", product.ref],
              ["Category", product.category],
              ["Condition", product.condition],
              ["City", product.city],
              ["Owner", product.seller_name],
              ["WhatsApp", `+91 ${product.mobile}`],
            ].map(([l, v]) => (
              <div className="spec-box" key={l}><div className="sp-label">{l}</div><div className="sp-val">{v}</div></div>
            ))}
          </div>
          <div className="pipe">Description</div>
          <p className="m-desc">{product.description || "WhatsApp message se bana public card — seller ki consent ke saath."}</p>
          <div className="modal-actions">
            <a className="btn primary" href={`https://wa.me/91${product.mobile}?text=${waText}`} target="_blank" rel="noopener noreferrer">WhatsApp par Baat Karein</a>
            <Link className="btn" to={`/list?${qs.toString()}`}>Is Data se Form Auto-fill Karein</Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Listing() {
  const [data, setData] = useState({ items: [], categories: [], cities: [] });
  const [filters, setFilters] = useState({ category: "", city: "", condition: "", min_price: "", max_price: "", q: "" });
  const [favs, setFavs] = useState(loadFavs);
  const [open, setOpen] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.products(filters).then(setData).catch((e) => setErr(e.message));
  }, [filters.category, filters.city, filters.condition, filters.min_price, filters.max_price, filters.q]);

  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));

  async function openCard(id) {
    try {
      setOpen(await api.product(id));
    } catch (e) {
      setErr(e.message);
    }
  }

  function toggleFav(id, e) {
    e.stopPropagation();
    const next = favs.includes(id) ? favs.filter((x) => x !== id) : [...favs, id];
    setFavs(next);
    saveFavs(next);
  }

  const conditions = useMemo(() => ["Brand New", "Like New", "Excellent", "Very Good", "Good", "Fair", "Average", "Used", "Refurbished"], []);

  return (
    <>
      <section className="hero">
        <h1 className="display">Purane Products,<br />Naye Khareedaar.</h1>
        <p className="lede">WhatsApp par product details bhejo, yahan card ban jata hai — naam aur number ke saath. Filters laga kar apne sheher aur budget ka saman dhundo.</p>
        <Link className="btn hero-cta" to="/meta">WhatsApp Webhook Setup →</Link>
      </section>
      <section className="filterbar" aria-label="Filters">
        <div className="f f-cat">
          <label htmlFor="fc">Category</label>
          <select id="fc" value={filters.category} onChange={(e) => set("category", e.target.value)}>
            <option value="">Sab</option>
            {data.categories.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div className="f f-city">
          <label htmlFor="fci">City</label>
          <select id="fci" value={filters.city} onChange={(e) => set("city", e.target.value)}>
            <option value="">Sab</option>
            {data.cities.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div className="f f-cond">
          <label htmlFor="fco">Condition</label>
          <select id="fco" value={filters.condition} onChange={(e) => set("condition", e.target.value)}>
            <option value="">Sab</option>
            {conditions.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div className="f f-min"><label htmlFor="fmin">Min Price (₹)</label><input id="fmin" type="number" min="0" placeholder="0" value={filters.min_price} onChange={(e) => set("min_price", e.target.value)} /></div>
        <div className="f f-max"><label htmlFor="fmax">Max Price (₹)</label><input id="fmax" type="number" min="0" placeholder="1,00,000" value={filters.max_price} onChange={(e) => set("max_price", e.target.value)} /></div>
        <div className="f f-q"><label htmlFor="fq">Search</label><input id="fq" type="search" placeholder="Activa, iPhone, sofa..." value={filters.q} onChange={(e) => set("q", e.target.value)} /></div>
        <div className="f-actions">
          <button className="btn small" type="button" onClick={() => setFilters({ category: "", city: "", condition: "", min_price: "", max_price: "", q: "" })}>Clear Filters</button>
          <span className="result-count">{data.items.length} card{data.items.length === 1 ? "" : "s"}</span>
        </div>
      </section>
      {err && <div className="err">{err}</div>}
      <div className="grid">
        {data.items.map((p) => {
          const fav = favs.includes(p.id);
          return (
            <article className="card" key={p.id} tabIndex={0} role="button" aria-label={`${p.title} — details kholen`}
              onClick={() => openCard(p.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCard(p.id); } }}>
              <div className="card-media">
                <span className="cat-badge">{p.category}</span>
                <button className={`icon-btn${fav ? " fav-active" : ""}`} aria-pressed={fav} aria-label={fav ? "Unfavorite" : "Favorite"} onClick={(e) => toggleFav(p.id, e)}>{fav ? "♥" : "♡"}</button>
                <div className="ph-block">{(p.category || "?").charAt(0).toUpperCase()}<span>{p.category} · Photo</span></div>
              </div>
              <div className="body">
                <div className="card-title">{p.title}</div>
                <div className="card-sub">{p.category} · {p.condition}</div>
                <div className="card-loc">{pinSvg()} {p.city}</div>
                <div className="price mono">₹{p.price}</div>
                <div className="seller"><span className="nm">{p.seller_name}</span> · <span className="ph">+91 {p.mobile}</span></div>
              </div>
            </article>
          );
        })}
      </div>
      {!data.items.length && (
        <div className="empty">
          <div className="big">Koi card nahi mila</div>
          Listing empty hai. WhatsApp webhook se message aane ke baad form publish karein, ya <Link to="/list" style={{ textDecoration: "underline" }}>direct form</Link> bhariye.
        </div>
      )}
      {open && <ProductModal product={open} onClose={() => setOpen(null)} />}
    </>
  );
}
