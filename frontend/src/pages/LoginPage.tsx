import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { useWorkspace } from "../context/WorkspaceContext";

export default function LoginPage() {
  const { login } = useWorkspace();
  const navigate = useNavigate();
  const [name, setName] = useState("Ananya Kapoor");
  const [error, setError] = useState("");
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) { setError("Enter your name to continue."); return; }
    login(name); navigate("/workspace");
  };
  return <main className="login-page"><section className="login-panel">
    <div className="brand-mark large">26<span>AS</span></div><p className="eyebrow">CA OFFICE TAX WORKSPACE</p>
    <h1>26AS Reconciliation<br />&amp; TDS Compliance</h1>
    <p className="login-copy">One CA-office workspace with two distinct work areas: reconcile the assessee’s Books and tax-credit sources, or review a deductor’s statutory TDS compliance.</p>
    <form className="login-form" onSubmit={submit} noValidate><label htmlFor="reviewer-name">Reviewer name</label><input id="reviewer-name" data-testid="login-name-input" value={name} onChange={(event) => { setName(event.target.value); setError(""); }} placeholder="Your name (recorded on identity decisions and notes)" autoComplete="name" aria-invalid={!!error} />{error && <small className="field-error" data-testid="login-error">{error}</small>}<button type="submit" className="primary-button login-button" data-testid="login-submit-button">Enter workspace <ArrowRight size={16} /></button></form>
    <div className="secure-note"><ShieldCheck size={15} /> Demo access · No client data leaves this workspace</div>
  </section><aside className="login-aside"><div className="aside-grid"><span>CA OFFICE CONTROL</span><strong>Know what matched.<br />Know why.<br />Know what to do next.</strong><div className="login-control-copy"><p><b>26AS Reconciliation</b> — Assessee PAN → 26AS deductors (TAN) → identity resolution → matching → claimability → CA review.</p><p><b>TDS Compliance</b> — Company TAN → payment ledger → PAN and rule evidence → deduction and deposit review → CA action.</p></div></div></aside></main>;
}
