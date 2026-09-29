import { useState } from "react";
import { Building2, ClipboardList, FileSearch, LockKeyhole, Plus, ShieldCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { ErrorNotice, InfoNotice, PageHeader } from "../components/common";
import { api, getErrorMessage } from "../services/api";

const empty = { organization_id: "", client_id: "", assessee_legal_name: "", assessee_pan: "", tan: "", financial_year: "", tax_year: "", quarter: "" };

export default function TdsCompliancePage() {
  const assignments = useQuery({ queryKey: ["tds-compliance-assignments"], queryFn: api.tdsComplianceAssignments });
  const [form, setForm] = useState(empty);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState("");
  const update = (key: keyof typeof empty, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const create = async (event: React.FormEvent) => {
    event.preventDefault(); setMessage("");
    if (!form.organization_id || !form.client_id || !form.assessee_legal_name || !/^\d{4}-\d{2}$/.test(form.financial_year)) {
      setMessage("Organisation, client, legal name and financial year (YYYY-YY) are required."); return;
    }
    setCreating(true);
    try { await api.createTdsComplianceAssignment({ ...form, status: "DRAFT" }); setForm(empty); await assignments.refetch(); }
    catch (error) { setMessage(getErrorMessage(error, "TDS Compliance is protected until the office OIDC provider is configured.")); }
    finally { setCreating(false); }
  };
  return <main className="page">
    <PageHeader eyebrow="TDS COMPLIANCE" title="Assignments" subtitle="Create the client and period record here. Complete each compliance activity from its own workspace." />
    <InfoNotice><ShieldCheck size={18} /> <b>Assignment register.</b> Payment Ledger, TDS Calculation, Deposit & Challans, Interest & Delay, Return Audit, Review Queue and Reports are separate workspaces. Select this assignment from any workspace to continue.</InfoNotice>
    {message && <ErrorNotice message={message} />}
    <section className="content-card">
      <div className="section-heading"><div><span className="eyebrow">NEW ASSIGNMENT</span><h2>Create draft assignment</h2><p>Set the client, tax identifiers and reporting period before starting TDS work.</p></div><Plus size={23} /></div>
      <form className="form-grid" onSubmit={create}>
        <label>Organisation ID<input value={form.organization_id} onChange={(event) => update("organization_id", event.target.value)} placeholder="Organisation ID" required /></label>
        <label>Client ID<input value={form.client_id} onChange={(event) => update("client_id", event.target.value)} placeholder="Client ID" required /></label>
        <label>Legal name<input value={form.assessee_legal_name} onChange={(event) => update("assessee_legal_name", event.target.value)} placeholder="Client legal name" required /></label>
        <label>PAN (optional)<input value={form.assessee_pan} onChange={(event) => update("assessee_pan", event.target.value.toUpperCase())} placeholder="ABCDE1234F" /></label>
        <label>TAN (optional)<input value={form.tan} onChange={(event) => update("tan", event.target.value.toUpperCase())} placeholder="ABCD12345E" /></label>
        <label>Financial year<input value={form.financial_year} onChange={(event) => update("financial_year", event.target.value)} placeholder="2026-27" required /></label>
        <label>Tax year (optional)<input value={form.tax_year} onChange={(event) => update("tax_year", event.target.value)} placeholder="2027-28" /></label>
        <label>Quarter (optional)<select value={form.quarter} onChange={(event) => update("quarter", event.target.value)}><option value="">All quarters</option><option value="Q1">Q1</option><option value="Q2">Q2</option><option value="Q3">Q3</option><option value="Q4">Q4</option></select></label>
        <div className="form-actions"><button className="primary-button" disabled={creating} type="submit">{creating ? "Creating..." : "Create draft assignment"}</button></div>
      </form>
    </section>
    <section className="content-card">
      <div className="section-heading"><div><span className="eyebrow">ASSIGNMENT REGISTER</span><h2>Current assignments</h2><p>Open the appropriate workspace for the selected client and period.</p></div><ClipboardList size={23} /></div>
      {assignments.isLoading && <p className="muted">Loading assignments...</p>}
      {assignments.isError && <ErrorNotice message="Assignments could not be loaded." />}
      {!assignments.isLoading && !assignments.isError && <div className="table-scroll"><table className="data-table"><thead><tr><th>Client / PAN</th><th>TAN</th><th>FY / quarter</th><th>Status</th><th>Open reviews</th><th>Last activity</th><th>Lock status</th><th /></tr></thead><tbody>
        {assignments.data?.map((item) => <tr key={item.assignment_id}>
          <td><b>{item.assessee_legal_name}</b><br /><small>{item.assessee_pan || "PAN not supplied"}</small></td><td>{item.tan || "TAN not supplied"}</td><td>{item.financial_year} / {item.quarter || "All quarters"}</td><td><span className="badge blue">{item.status}</span></td>
          <td><a className="text-link" href={"/tds-compliance/review-queue?assignment=" + encodeURIComponent(item.assignment_id)}>Open review queue</a></td><td>{item.updated_at ? new Date(item.updated_at).toLocaleString() : "-"}</td><td><span className={"badge " + (item.status === "LOCKED" ? "red" : "blue")}>{item.status === "LOCKED" ? "Locked" : "Open"}</span></td>
          <td><div className="table-actions"><a className="mini-button" href={"/tds-compliance/assignments/" + encodeURIComponent(item.assignment_id) + "/plan"}><Building2 size={14} />Open Plan</a><a className="mini-button" href={"/tds-compliance/assignments/" + encodeURIComponent(item.assignment_id) + "/plan"}>Continue</a><a className="mini-button" href={"/tds-compliance/return-audit?assignment=" + encodeURIComponent(item.assignment_id)}><FileSearch size={14} />Review</a>{item.status === "LOCKED" && <LockKeyhole size={16} aria-label="Assignment locked" />}</div></td>
        </tr>)}{!assignments.data?.length && <tr><td colSpan={8} className="muted">No assignments yet. Create a draft assignment to begin.</td></tr>}
      </tbody></table></div>}
    </section>
  </main>;
}
