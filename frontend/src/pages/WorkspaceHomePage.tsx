import { ArrowRight, Building2, Calculator, FileSearch, ShieldCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/common";

export default function WorkspaceHomePage() {
  const navigate = useNavigate();
  return <>
    <PageHeader eyebrow="CA OFFICE TAX WORKSPACE" title="Choose your workspace" subtitle="Select the work area for this client engagement. Each workspace keeps its own process, controls and audit trail." />
    <section className="workspace-module-grid" aria-label="Choose a workspace">
      <article className="workspace-module-card reconciliation"><div className="workspace-module-icon"><FileSearch size={25} /></div><span className="eyebrow">ASSESSEE-SIDE</span><h2>26AS Reconciliation</h2><p>Choose a client, upload Books and 26AS sources, reconcile tax credits, and review exceptions and claimability.</p><ul><li>Client directory and completed runs</li><li>Books, TDS receivable and 26AS matching</li><li>Exceptions, identity review and reports</li></ul><button className="primary-button" onClick={() => navigate("/dashboard?directory=1")}>Enter 26AS Reconciliation <ArrowRight size={16} /></button></article>
      <article className="workspace-module-card compliance"><div className="workspace-module-icon"><ShieldCheck size={25} /></div><span className="eyebrow">DEDUCTOR-SIDE</span><h2>TDS Compliance</h2><p>Work from the payment ledger through TDS calculation, deposit evidence, due dates, interest and audit-ready compliance review.</p><ul><li>Assignments and payment-ledger controls</li><li>TDS Calculator with configured rules</li><li>Deposit compliance and interest review</li></ul><button className="primary-button" onClick={() => navigate("/tds-compliance")}>Enter TDS Compliance <ArrowRight size={16} /></button></article>
    </section>
    <section className="workspace-section workspace-home-note"><Building2 size={18} /><div><b>Client context follows the selected workspace</b><p>26AS Reconciliation starts with the client directory. TDS Compliance starts with its separate deductor-side assignment workflow.</p></div><Calculator size={18} /></section>
  </>;
}
