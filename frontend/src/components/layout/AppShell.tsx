import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, Bell, Bot, Calculator, ChevronRight, CircleHelp, ClipboardCheck, CloudUpload, FileCheck2, FileSearch, Landmark, LayoutDashboard, ListChecks, Loader2, LogOut, Menu, PanelLeftClose, PanelLeftOpen, Settings, ShieldCheck, Tags, TimerReset } from "lucide-react";
import { AIAssistant } from "../AIAssistant";
import { ClientRunSelector } from "../ClientRunSelector";
import { useWorkspace } from "../../context/WorkspaceContext";

export const RECONCILIATION_NAV = [
  { label: "Dashboard", path: "/dashboard", icon: LayoutDashboard },
  { label: "New Reconciliation", path: "/reconciliation/new", icon: CloudUpload },
  { label: "Reconciliation", path: "/reconciliation", icon: ClipboardCheck },
  { label: "Exceptions", path: "/exceptions", icon: AlertCircle },
  { label: "Identity Review", path: "/identity-review", icon: Tags },
  { label: "Reports", path: "/reports", icon: FileCheck2 },
];
export const WORKSPACE_NAV = [{ label: "Choose Workspace", path: "/workspace", icon: LayoutDashboard }];

export const TDS_COMPLIANCE_NAV = [
  { label: "Compliance Overview", path: "/tds-compliance", icon: ShieldCheck },
  { label: "Assignments", path: "/tds-compliance/assignments", icon: ClipboardCheck },
  { label: "Plan", path: "/tds-compliance/plan", icon: ListChecks },
  { label: "Payment Ledger", path: "/tds-compliance/ledger", icon: CloudUpload },
  { label: "TDS Calculation", path: "/tds-compliance/calculation", icon: Calculator },
  { label: "Deposit & Challans", path: "/tds-compliance/deposit", icon: Landmark },
  { label: "Interest & Delay", path: "/tds-compliance/interest", icon: TimerReset },
  { label: "Return Audit", path: "/tds-compliance/return-audit", icon: FileSearch },
  { label: "Review Queue", path: "/tds-compliance/review-queue", icon: Tags },
  { label: "Reports", path: "/tds-compliance/reports", icon: FileCheck2 },
];
const TDS_NAV_GROUPS = [
  { label: "TDS COMPLIANCE", items: TDS_COMPLIANCE_NAV.slice(0, 2) },
  { label: "CURRENT ASSIGNMENT", items: TDS_COMPLIANCE_NAV.slice(2) },
  { label: "ADMINISTRATION", items: [{ label: "Administration", path: "/tds-compliance/administration", icon: Settings }, { label: "Statutory Rules", path: "/tds-compliance/settings", icon: Settings }] },
];
const UTILITY = [{ label: "Settings", path: "/settings", icon: Settings }, { label: "Help", path: "/help", icon: CircleHelp }];
const TDS_COMPLIANCE_UTILITY = [{ label: "Settings", path: "/tds-compliance/settings", icon: Settings }, { label: "Help", path: "/tds-compliance/help", icon: CircleHelp }];
const testId = (label: string) => `navigation-${label.toLowerCase().replaceAll(" ", "-")}`;

const titleFor = (path: string) => {
  if (path === "/reconciliation/new") return "New Reconciliation";
  if (path.startsWith("/reconciliation/")) return "Transaction Detail";
  return [...RECONCILIATION_NAV, ...TDS_COMPLIANCE_NAV, ...UTILITY].find((item) => path.startsWith(item.path.split("#")[0]))?.label || "Dashboard";
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout, run, clientId, selectClient, selectRun, processing } = useWorkspace();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("26as-sidebar") === "collapsed");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiResultId, setAiResultId] = useState<string | null>(null);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [selectorClientId, setSelectorClientId] = useState<string | null>(null);
  const workspaceLanding = location.pathname === "/workspace";
  const complianceModule = location.pathname.startsWith("/tds-compliance");
  const navigation = workspaceLanding ? WORKSPACE_NAV : complianceModule ? TDS_COMPLIANCE_NAV : RECONCILIATION_NAV;

  useEffect(() => { localStorage.setItem("26as-sidebar", collapsed ? "collapsed" : "open"); }, [collapsed]);
  useEffect(() => { setMobileOpen(false); setNotifOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!location.hash) return;
    const targetId = location.hash.slice(1);
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scrollToTarget = () => {
      const target = document.getElementById(targetId);
      if (target) { target.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
      if (++attempts < 8) timer = setTimeout(scrollToTarget, 75);
    };
    timer = setTimeout(scrollToTarget, 0);
    return () => { if (timer) clearTimeout(timer); };
  }, [location.hash, location.pathname]);
  useEffect(() => {
    const openAI = (event: Event) => {
      const detail = (event as CustomEvent<{ resultId?: string; runId?: string }>).detail;
      if (detail?.runId && detail.runId !== run?.run_id) selectRun(detail.runId);
      setAiResultId(detail?.resultId || null); setAiOpen(true);
    };
    window.addEventListener("open-ai-assistant", openAI);
    return () => window.removeEventListener("open-ai-assistant", openAI);
  }, [run?.run_id, selectRun]);
  useEffect(() => {
    const openSelector = (event: Event) => { const clientId = (event as CustomEvent<{ clientId?: string }>).detail?.clientId || null; setSelectorClientId(clientId); setSelectorOpen(true); };
    window.addEventListener("open-client-selector", openSelector);
    return () => window.removeEventListener("open-client-selector", openSelector);
  }, []);

  // Opening the picker must not discard the current completed workspace. The
  // switch is committed only after the CA selects a completed run.
  // In 26AS, changing a client is a full workspace decision. Open the client
  // directory instead of forcing the CA through a search dialog.
  const changeClient = () => { if (!complianceModule) { navigate("/dashboard?directory=1"); return; } setSelectorClientId(null); setSelectorOpen(true); };
  const changeRun = () => { setSelectorClientId(clientId); setSelectorOpen(true); };
  const startClientReconciliation = (id: string) => { selectClient(id); setSelectorOpen(false); navigate("/reconciliation/new"); };
  const exceptionCount = run?.summary?.exceptions_count || 0;
  const identityCount = run?.summary?.identity_review_count || 0;
  const badge: Record<string, number> = { "/exceptions": exceptionCount, "/identity-review": identityCount };
  const isActive = (path: string) => {
    const [pathname, hash] = path.split("#");
    if (hash) return location.pathname === pathname && location.hash === `#${hash}`;
    if (pathname === "/tds-compliance") return location.pathname === pathname;
    return pathname === "/reconciliation" ? location.pathname.startsWith("/reconciliation") && location.pathname !== "/reconciliation/new" : location.pathname.startsWith(pathname);
  };
  const NavButton = ({ label, path, icon: Icon }: { label: string; path: string; icon: React.ElementType }) => (
    <button className={`nav-item ${isActive(path) ? "active" : ""}`} data-testid={testId(label)} title={collapsed ? label : undefined} aria-current={isActive(path) ? "page" : undefined} onClick={() => navigate(path === "/reconciliation/new" ? (clientId ? "/reconciliation/new" : "/reconciliation/new?new_client=1") : path)}><Icon size={17} /><span>{label}</span>{badge[path] ? <em data-testid={`${testId(label)}-count`}>{badge[path]}</em> : null}</button>
  );

  return <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
    {mobileOpen && <div className="mobile-backdrop" onClick={() => setMobileOpen(false)} aria-hidden="true" />}
    <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`} aria-label="Sidebar">
      <div className="sidebar-brand"><div className="brand-mark">26<span>AS</span></div><div className="brand-text"><b>{workspaceLanding ? "CA Tax Workspace" : complianceModule ? "TDS Compliance" : "26AS Reconciliation"}</b><small>{workspaceLanding ? "Choose a work area" : complianceModule ? "CA Compliance Workspace" : "CA Office Workspace"}</small></div><button className="icon-button sidebar-toggle" data-testid="sidebar-collapse-button" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></div>
      <nav className="main-nav" aria-label={workspaceLanding ? "Workspace navigation" : complianceModule ? "TDS Compliance navigation" : "Reconciliation navigation"}>{complianceModule ? TDS_NAV_GROUPS.map(group => <div className="nav-group" key={group.label}><span>{group.label}</span>{group.items.map(item => <NavButton key={item.path} {...item} />)}</div>) : navigation.map((item) => <NavButton key={item.path} {...item} />)}</nav>
      <div className="sidebar-bottom">{(workspaceLanding ? [] : complianceModule ? TDS_COMPLIANCE_UTILITY : UTILITY).map((item) => <NavButton key={item.path} {...item} />)}<div className="user-mini"><div className="avatar" aria-hidden="true">{user.initials}</div><div className="brand-text"><b>{user.name}</b><small>{user.role}</small></div><button className="icon-button" data-testid="logout-button" aria-label="Log out" title="Log out" onClick={() => { logout(); navigate("/"); }}><LogOut size={15} /></button></div></div>
    </aside>
    <div className="content-wrap"><header className="topbar">
      <button className="mobile-menu icon-button" data-testid="mobile-menu-button" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu size={20} /></button>
      <nav className="breadcrumb" aria-label="Breadcrumb"><span>Workspace</span><ChevronRight size={13} /><b>{workspaceLanding ? "Choose workspace" : complianceModule ? "TDS Compliance" : titleFor(location.pathname)}</b></nav>
      <div className="topbar-actions">
        {!workspaceLanding && !complianceModule && processing && <span className="processing-chip" data-testid="processing-indicator"><Loader2 size={13} className="spin" /> Processing</span>}
        {!workspaceLanding && !complianceModule && <button className="secondary-button ai-launch" data-testid="ai-assistant-button" disabled={!run || run.status !== "COMPLETED"} onClick={() => { setAiResultId(null); setAiOpen(true); }}><Bot size={14} /> Ask AI</button>}
        {!workspaceLanding && run && <span className="workspace-current"><span className={`status-dot ${!complianceModule && run.status === "FAILED" ? "red" : !complianceModule && run.status === "PROCESSING" ? "amber" : ""}`} />{run.assessee_name || "Unknown assessee"}<small>FY {run.financial_year || "â€”"}{!complianceModule && ` Â· ${run.status === "COMPLETED" ? "Reconciliation completed" : run.status}`}</small></span>}
        {!workspaceLanding && <button className="secondary-button ai-launch" onClick={changeClient}>{run ? "Change Client" : "Select Client"}</button>}
        {!workspaceLanding && !complianceModule && run && <button className="secondary-button ai-launch" onClick={changeRun}>Change Run</button>}
        {!workspaceLanding && !complianceModule && <div className="notif-wrap"><button className={`icon-button notification-button ${exceptionCount ? "has-dot" : ""}`} data-testid="notifications-button" aria-label="Notifications" aria-expanded={notifOpen} onClick={() => setNotifOpen((open) => !open)}><Bell size={17} /></button>{notifOpen && <div className="notif-panel" data-testid="notifications-panel" role="menu"><b>Attention required</b>{!run ? <p>No reconciliation run yet. Upload Books and 26AS to begin.</p> : <><button role="menuitem" onClick={() => navigate("/exceptions")}><AlertCircle size={14} /> {exceptionCount} exception{exceptionCount === 1 ? "" : "s"} need review</button><button role="menuitem" onClick={() => navigate("/identity-review")}><Tags size={14} /> {identityCount} identit{identityCount === 1 ? "y" : "ies"} awaiting decision</button></>}</div>}</div>}
        <div className="avatar" title={user.name}>{user.initials}</div>
      </div>
    </header><main className="main-content" id="main">{children}</main>
      {!workspaceLanding && !complianceModule && <AIAssistant run={run} open={aiOpen} initialResultId={aiResultId} onClose={() => setAiOpen(false)} />}
      <ClientRunSelector open={selectorOpen} initialClientId={selectorClientId} onClose={() => setSelectorOpen(false)} onSelectClient={selectClient} onSelectRun={(id) => { selectRun(id); setSelectorOpen(false); navigate("/dashboard"); }} onStartNewClient={startClientReconciliation} />
    </div>
  </div>;
}
