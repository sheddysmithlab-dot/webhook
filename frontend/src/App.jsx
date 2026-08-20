import { Route, Routes } from "react-router-dom";
import Listing from "./pages/Listing.jsx";
import ListForm from "./pages/ListForm.jsx";
import Webhook from "./pages/Webhook.jsx";
import Admin from "./pages/Admin.jsx";
import InfraDealerIntegration from "./pages/InfraDealerIntegration.jsx";
import Login, { RequireAuth } from "./pages/Login.jsx";
import AdminShell from "./layout/AdminShell.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/meta" element={<RequireAuth><AdminShell flush><Webhook /></AdminShell></RequireAuth>} />
      <Route path="/" element={<RequireAuth><AdminShell><Listing /></AdminShell></RequireAuth>} />
      <Route path="/list" element={<RequireAuth><AdminShell><ListForm /></AdminShell></RequireAuth>} />
      <Route path="/admin" element={<RequireAuth><AdminShell><Admin /></AdminShell></RequireAuth>} />
      <Route path="/admin/infradealer" element={<RequireAuth><AdminShell><InfraDealerIntegration /></AdminShell></RequireAuth>} />
    </Routes>
  );
}
