import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "./components/ui/sonner";
import "./App.css";
import "./styles/workspace.css";
import { AppShell } from "./components/layout/AppShell";
import { useWorkspace, WorkspaceProvider } from "./context/WorkspaceContext";
import DashboardPage from "./pages/DashboardPage";
import ExceptionsPage from "./pages/ExceptionsPage";
import HelpPage from "./pages/HelpPage";
import IdentityReviewPage from "./pages/IdentityReviewPage";
import LoginPage from "./pages/LoginPage";
import NewReconciliationPage from "./pages/NewReconciliationPage";
import ReconciliationPage from "./pages/ReconciliationPage";
import SalesTds26asPage from "./pages/SalesTds26asPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";
import TdsCompliancePage from "./pages/TdsCompliancePage";
import TdsCalculatorPage from "./pages/TdsCalculatorPage";
import TdsDepositPage from "./pages/TdsDepositPage";
import TdsComplianceSettingsPage from "./pages/TdsComplianceSettingsPage";
import TdsComplianceOverviewPage from "./pages/TdsComplianceOverviewPage";
import TdsReturnAuditPage from "./pages/TdsReturnAuditPage";
import TdsAssignmentPlanPage, { TdsPlanLandingPage } from "./pages/TdsAssignmentPlanPage";
import { TdsAdministrationPage, TdsCalculationWorkbenchPage, TdsInterestPage, TdsLedgerPage, TdsReportsPage, TdsReviewQueuePage } from "./pages/TdsWorkbenchPages";
import WorkspaceHomePage from "./pages/WorkspaceHomePage";


function Routed() {
  const { user, workspaceHydrating } = useWorkspace();
  if (!user.name) return <LoginPage />;
  if (workspaceHydrating) return <main className="workspace-hydrating" data-testid="workspace-hydrating"><div className="brand-mark">26<span>AS</span></div><p>Loading workspace…</p></main>;
  return <AppShell><Routes>
    <Route path="/" element={<Navigate to="/workspace" replace />} />
    <Route path="/workspace" element={<WorkspaceHomePage />} />
    <Route path="/dashboard" element={<DashboardPage />} />
    {/* Keep this explicit static route ahead of result routes: `new` must
        never be interpreted as a reconciliation-result identifier. */}
    <Route path="/reconciliation/new" element={<NewReconciliationPage />} />
    <Route path="/tds-compliance" element={<TdsComplianceOverviewPage />} />
    <Route path="/tds-compliance/assignments" element={<TdsCompliancePage />} />
    <Route path="/tds-compliance/plan" element={<TdsPlanLandingPage />} />
    <Route path="/tds-compliance/assignments/:assignmentId/plan" element={<TdsAssignmentPlanPage />} />
    <Route path="/tds-compliance/ledger" element={<TdsLedgerPage />} />
    <Route path="/tds-compliance/calculation" element={<TdsCalculationWorkbenchPage />} />
    <Route path="/tds-compliance/calculator" element={<TdsCalculatorPage />} />
    <Route path="/tds-compliance/deposit" element={<TdsDepositPage />} />
    <Route path="/tds-compliance/interest" element={<TdsInterestPage />} />
    <Route path="/tds-compliance/return-audit" element={<TdsReturnAuditPage />} />
    <Route path="/tds-compliance/review-queue" element={<TdsReviewQueuePage />} />
    <Route path="/tds-compliance/reports" element={<TdsReportsPage />} />
    <Route path="/tds-compliance/administration" element={<TdsAdministrationPage />} />
    <Route path="/tds-compliance/settings" element={<TdsComplianceSettingsPage />} />
    <Route path="/tds-compliance/help" element={<HelpPage />} />
    <Route path="/reconciliation/:id" element={<ReconciliationRoute />} />
    <Route path="/reconciliation" element={<ReconciliationRoute />} />
    <Route path="/exceptions" element={<ExceptionsPage />} />
    <Route path="/identity-review" element={<IdentityReviewPage />} />
    <Route path="/reports" element={<ReportsPage />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/help" element={<HelpPage />} />
    <Route path="*" element={<Navigate to="/dashboard" replace />} />
  </Routes></AppShell>;
}

function ReconciliationRoute() {
  const { run } = useWorkspace();
  return run?.workflow === "SALES_TDS_26AS" ? <SalesTds26asPage /> : <ReconciliationPage />;
}

export default function App() {
  return <BrowserRouter><WorkspaceProvider><Routed /><Toaster position="bottom-right" richColors closeButton /></WorkspaceProvider></BrowserRouter>;
}
