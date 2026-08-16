import { NavLink, Route, Routes } from "react-router-dom";
import Listing from "./pages/Listing.jsx";
import ListForm from "./pages/ListForm.jsx";
import Webhook from "./pages/Webhook.jsx";
import Admin from "./pages/Admin.jsx";

export default function App() {
  return (
    <>
      <a className="skip-link" href="#app">Main content par jayein</a>
      <header className="topbar">
        <div className="container">
          <NavLink className="brand" to="/">infra<span>dealer</span></NavLink>
          <span className="tag">WhatsApp → Product Flow</span>
        </div>
      </header>
      <nav className="nav">
        <div className="container" id="mainnav">
          <NavLink to="/" end>Listing</NavLink>
          <NavLink to="/meta">WhatsApp Webhook</NavLink>
          <NavLink to="/admin">Admin Panel</NavLink>
        </div>
      </nav>
      <main className="container" id="app" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Listing />} />
          <Route path="/list" element={<ListForm />} />
          <Route path="/meta" element={<Webhook />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </main>
      <footer>
        <div className="container">
          <div><b>infradealer</b> — WhatsApp se product list karein</div>
          <div>Meta Cloud API webhook + OTP · Data Python backend (SQLite) mein persist hota hai</div>
        </div>
      </footer>
    </>
  );
}
