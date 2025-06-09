import { Route, Routes } from "react-router-dom";

import { Layout, Protected } from "./components/Layout";
import { Audit, Organisations, Reports, Settings, Unmatched, Users } from "./pages/Admin";
import Home from "./pages/Home";
import { InvoiceDetail, InvoiceList } from "./pages/Invoices";
import { LeaseDetail, LeaseForm, LeaseList } from "./pages/Leases";
import InvitePage from "./pages/Invite";
import Login from "./pages/Login";
import { MaintenanceDetail, MaintenanceList } from "./pages/Maintenance";
import Pay from "./pages/Pay";
import Preferences from "./pages/Preferences";
import Privacy from "./pages/Privacy";
import { PropertyDetail, PropertyList } from "./pages/Properties";
import MyDocuments from "./pages/MyDocuments";
import Tenants, { TenantDetail } from "./pages/Tenants";

const STAFF = ["org_admin", "property_manager"] as const;

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/login/:handle" element={<Login />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route element={<Protected />}>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="invoices" element={<InvoiceList />} />
          <Route path="invoices/:id" element={<InvoiceDetail />} />
          <Route path="pay/:id" element={<Pay />} />
          <Route path="leases" element={<LeaseList />} />
          <Route path="leases/:id" element={<LeaseDetail />} />
          <Route path="maintenance" element={<MaintenanceList />} />
          <Route path="maintenance/:id" element={<MaintenanceDetail />} />
          <Route element={<Protected roles={[...STAFF]} />}>
            <Route path="properties" element={<PropertyList />} />
            <Route path="properties/:id" element={<PropertyDetail />} />
            <Route path="tenants" element={<Tenants />} />
            <Route path="tenants/:id" element={<TenantDetail />} />
            <Route path="leases/new" element={<LeaseForm />} />
            <Route path="reports" element={<Reports />} />
          </Route>
          <Route element={<Protected roles={["org_admin"]} />}>
            <Route path="payments/unmatched" element={<Unmatched />} />
            <Route path="users" element={<Users />} />
            <Route path="settings" element={<Settings />} />
            <Route path="audit" element={<Audit />} />
          </Route>
          <Route element={<Protected roles={["org_admin", "tenant"]} />}><Route path="privacy" element={<Privacy />} /></Route>
          <Route element={<Protected roles={["tenant"]} />}>
            <Route path="preferences" element={<Preferences />} />
            <Route path="documents" element={<MyDocuments />} />
          </Route>
          <Route element={<Protected roles={["platform_admin"]} />}><Route path="platform/organizations" element={<Organisations />} /></Route>
        </Route>
      </Route>
    </Routes>
  );
}
