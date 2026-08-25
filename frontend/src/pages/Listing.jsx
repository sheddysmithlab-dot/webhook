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

function fmtPrice(n) {
  const x = Number(n);
  if (!x || x <= 0 || (x >= 1900 && x <= 2035)) return "Rate puchhein";
  return `₹${x.toLocaleString("en-IN")}`;
}

function Photos({ photos, category, showCount = false, size = "card" }) {
  const [i, setI] = useState(0);
  const [broken, setBroken] = useState({});
  const list = useMemo(
    () => (photos || []).filter((p) => p && p.url && !broken[p.id || p.url]),
    [photos, broken],
  );

  useEffect(() => {
    setI(0);
    setBroken({});
  }, [photos]);

  if (!list.length) {
    return (
      <div className={`ph-block${size === "lg" ? " lg" : ""}`}>
        <span className="ph-letter">{(category || "?").charAt(0).toUpperCase()}</span>
        <span>{category || "Listing"} · Photo unavailable</span>
      </div>
    );
  }

  const cur = list[Math.min(i, list.length - 1)];
  const idx = Math.min(i, list.length - 1);

  function go(n, e) {
    if (e) e.stopPropagation();
    setI((list.length + n) % list.length);
  }

  return (
    <>
      <img
        className="listing-photo"
        src={cur.url}
        alt=""
        loading="lazy"
        onError={() => setBroken((b) => ({ ...b, [cur.id || cur.url]: true }))}
      />
      {list.length > 1 && (
        <>
          <button type="button" className="photo-nav prev" aria-label="Previous photo" onClick={(e) => go(idx - 1, e)}>‹</button>
          <button type="button" className="photo-nav next" aria-label="Next photo" onClick={(e) => go(idx + 1, e)}>›</button>
          <div className="photo-dots">
            {list.map((p, n) => (
              <button
                key={p.id || p.url || n}
                type="button"
                className={n === idx ? "on" : ""}
                aria-label={`Photo ${n + 1}`}
                onClick={(e) => { e.stopPropagation(); setI(n); }}
              />
            ))}
          </div>
        </>
      )}
      {showCount && (
        <span className="gcount">{idx + 1}/{list.length}</span>
      )}
    </>
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

  const waText = encodeURIComponent(`Hi! Aapke ${product.title} ke baare mein baat karni hai (InfraDealer se).`);
  const qs = new URLSearchParams({
    title: product.title || "",
    cat: product.category || "",
    price: String(product.price || ""),
    cond: product.condition || "",
    city: product.city || "",
    mobile: product.mobile || "",
    name: product.seller_name || "",
  });

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={product.title}>
        <button className="modal-close" aria-label="Close" onClick={onClose}>×</button>
        <div className="gallery">
          <span className="cat-badge lg">{product.category}</span>
          <Photos photos={product.photos} category={product.category} showCount size="lg" />
        </div>
        <div className="modal-body">
          <div className="m-title">{product.title}</div>
          <div className="m-sub">{product.category} · {product.condition}</div>
          <div className="verified"><span className="v-badge">✓</span> Verified Listing · <span className="mono">{product.ref}</span></div>
          <div className="meta-row">
            <span>{pinSvg()} {product.city || "—"}</span>
            <span>Posted {fmtTime(product.created_at)}</span>
            <span>{product.views || 0} views</span>
          </div>
          <div className="pricebox">
            <div className="pb-label">Asking Price</div>
            <div className="pb-price mono">{fmtPrice(product.price)}</div>
            <div className="pb-meta">Last updated: {fmtTime(product.updated_at || product.created_at)}</div>
          </div>
          <div className="pipe">Key Details</div>
          <div className="spec-grid">
            {[
              ["Listing ID", product.ref],
              ["Category", product.category],
              ["Condition", product.condition],
              ["City", product.city || "—"],
              ["Owner", product.seller_name || "—"],
              ["WhatsApp", product.mobile ? `+91 ${product.mobile}` : "—"],
            ].map(([l, v]) => (
              <div className="spec-box" key={l}><div className="sp-label">{l}</div><div className="sp-val">{v || "—"}</div></div>
            ))}
          </div>
          <div className="pipe">Description</div>
          <p className="m-desc">{product.description || "WhatsApp se bana public card — seller ki consent ke saath."}</p>
          <div className="modal-actions">
            {product.mobile && (
              <a className="btn primary" href={`https://wa.me/91${product.mobile}?text=${waText}`} target="_blank" rel="noopener noreferrer">WhatsApp par Baat Karein</a>
            )}
            <Link className="btn" to={`/list?${qs.toString()}`}>Is Data se Form Auto-fill Karein</Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Listing() {
  const [data, setData] = useState({ items: [], categories: [], cities: [] });
  const [filters, setFilters] = useState({ category: "", city: "", condition: "", min_price: "", max_price: "", q: "" });
  const [favs, setFavs] = useState(loadFavs);
  const [open, setOpen] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const debouncedQ = useDebounced(filters.q, 280);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr("");
    const params = { ...filters, q: debouncedQ };
    api.products(params)
      .then((res) => {
        if (!cancelled) setData(res || { items: [], categories: [], cities: [] });
      })
      .catch((e) => {
        if (!cancelled) setErr(e.message || "Listings load nahi ho payi");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [filters.category, filters.city, filters.condition, filters.min_price, filters.max_price, debouncedQ]);

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

  const conditions = useMemo(
    () => ["Brand New", "Like New", "Excellent", "Very Good", "Good", "Fair", "Average", "Used", "Refurbished"],
    [],
  );

  return (
    <>
      <section className="hero listing-hero">
        <h1 className="display">InfraDealer Listings</h1>
        <p className="lede">WhatsApp se aayi truck / JCB / machinery cards yahan live hoti hain — photo, rate aur seller contact ke saath.</p>
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
        <div className="f f-max"><label htmlFor="fmax">Max Price (₹)</label><input id="fmax" type="number" min="0" placeholder="Any" value={filters.max_price} onChange={(e) => set("max_price", e.target.value)} /></div>
        <div className="f f-q"><label htmlFor="fq">Search</label><input id="fq" type="search" placeholder="Tata, JCB, Tipper..." value={filters.q} onChange={(e) => set("q", e.target.value)} /></div>
        <div className="f-actions">
          <button className="btn small" type="button" onClick={() => setFilters({ category: "", city: "", condition: "", min_price: "", max_price: "", q: "" })}>Clear Filters</button>
          <span className="result-count">{loading ? "Loading…" : `${data.items.length} card${data.items.length === 1 ? "" : "s"}`}</span>
        </div>
      </section>
      {err && <div className="err">{err}</div>}
      {loading && !data.items.length ? (
        <div className="grid listing-skel">
          {[1, 2, 3].map((n) => <div className="card skel" key={n}><div className="card-media" /><div className="body"><div className="skel-line" /><div className="skel-line short" /></div></div>)}
        </div>
      ) : (
        <div className="grid">
          {data.items.map((p) => {
            const fav = favs.includes(p.id);
            return (
              <article
                className="card"
                key={p.id}
                tabIndex={0}
                role="button"
                aria-label={`${p.title} — details kholen`}
                onClick={() => openCard(p.id)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCard(p.id); } }}
              >
                <div className="card-media">
                  <span className="cat-badge">{p.category}</span>
                  <button className={`icon-btn${fav ? " fav-active" : ""}`} aria-pressed={fav} aria-label={fav ? "Unfavorite" : "Favorite"} onClick={(e) => toggleFav(p.id, e)}>{fav ? "♥" : "♡"}</button>
                  <Photos photos={p.photos} category={p.category} />
                </div>
                <div className="body">
                  <div className="card-title">{p.title}</div>
                  <div className="card-sub">{p.category} · {p.condition}</div>
                  <div className="card-loc">{pinSvg()} {p.city || "—"}</div>
                  <div className="price mono">{fmtPrice(p.price)}</div>
                  <div className="seller">
                    <span className="nm">{p.seller_name || "Seller"}</span>
                    {p.mobile ? <> · <span className="ph">+91 {p.mobile}</span></> : null}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {!loading && !data.items.length && (
        <div className="empty">
          <div className="big">Koi card nahi mila</div>
          Listing empty hai. WhatsApp webhook se message aane ke baad form publish karein, ya <Link to="/list" style={{ textDecoration: "underline" }}>direct form</Link> bhariye.
        </div>
      )}
      {open && <ProductModal product={open} onClose={() => setOpen(null)} />}
    </>
  );
}
