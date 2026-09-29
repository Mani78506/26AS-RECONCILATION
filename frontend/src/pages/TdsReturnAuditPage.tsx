import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { InfoNotice, PageHeader } from "../components/common";
import { api } from "../services/api";

const show = (v: unknown) => v == null ? "Unavailable" : String(v);
const tabs = ["Return Summary", "Payment vs Return", "Expected TDS vs Return TDS", "Challan/Deposit Comparison", "Interest Evidence", "Exceptions"];
export default function TdsReturnAuditPage() {
  const assignments = useQuery({ queryKey: ["tds-compliance-assignments"], queryFn: api.tdsComplianceAssignments });
  const [searchParams] = useSearchParams();
  const [assignmentId, setAssignmentId] = useState("");
  const [artifactId, setArtifactId] = useState("");
  const [calculationId, setCalculationId] = useState("");
  const [selectedRun, setSelectedRun] = useState("");
  const [tab, setTab] = useState(tabs[0]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewResultId, setReviewResultId] = useState("");
  const [reviewDecision, setReviewDecision] = useState("KEEP_REVIEW");
  const [reviewReason, setReviewReason] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { const requested = searchParams.get("assignment"); if (requested && assignments.data?.some(item => item.assignment_id === requested) && requested !== assignmentId) setAssignmentId(requested); else if (!assignmentId && assignments.data?.[0]) setAssignmentId(assignments.data[0].assignment_id); }, [assignmentId, assignments.data, searchParams]);
  const artifacts = useQuery({ queryKey: ["return-audit-artifacts", assignmentId], queryFn: () => api.returnAuditArtifacts(assignmentId), enabled: Boolean(assignmentId) });
  const calculations = useQuery({ queryKey: ["return-audit-calculations", assignmentId], queryFn: () => api.tdsCalculations(assignmentId), enabled: Boolean(assignmentId) });
  const results = useQuery({ queryKey: ["return-audit-results", assignmentId], queryFn: () => api.returnAuditResults(assignmentId), enabled: Boolean(assignmentId) });
  const summary = useQuery({ queryKey: ["return-audit-summary", assignmentId, selectedRun], queryFn: () => api.returnAuditSummary(assignmentId, selectedRun), enabled: Boolean(assignmentId) });
  const runId = selectedRun || summary.data?.audit_run_id;
  const decisions = useQuery({ queryKey: ["return-audit-review-decisions", assignmentId, runId], queryFn: () => api.returnAuditReviewDecisions(assignmentId, runId), enabled: Boolean(assignmentId && runId) });
  const rows = (results.data || []).filter(r => r.audit_run_id === runId);
  const assignment = assignments.data?.find(item => item.assignment_id === assignmentId);
  const upload = async (file?: File) => {
    if (!file || !assignmentId) return;
    setBusy(true);
    try { const item = await api.uploadReturnAuditArtifact(assignmentId, file); setArtifactId(item.artifact_id); setMessage(`Stored ${item.filename}. ${item.parser_message}`); await artifacts.refetch(); }
    catch { setMessage("Return artifact could not be uploaded."); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const run = async () => {
    setBusy(true);
    try { const item = await api.runReturnAudit(assignmentId, artifactId, calculationId); setSelectedRun(item.audit_run.audit_run_id); setMessage(`Audit: ${item.audit_run.overall_status}.`); await Promise.all([results.refetch(), summary.refetch()]); }
    catch (e: any) { setMessage(e?.response?.data?.detail || "Return audit could not run."); }
    finally { setBusy(false); }
  };
  const decide = async () => {
    if (!reviewResultId || !reviewReason.trim()) { setMessage("Select a result and give the CA review reason."); return; }
    setBusy(true);
    try { const item = await api.createReturnAuditReviewDecision(assignmentId, reviewResultId, reviewDecision, reviewReason); setMessage(`Review decision ${item.decision} recorded as ${item.decision_id}.`); setReviewReason(""); setReviewResultId(""); await decisions.refetch(); }
    catch (e: any) { setMessage(e?.response?.data?.detail || "Review decision could not be recorded."); }
    finally { setBusy(false); }
  };
  return <><PageHeader eyebrow="PHASE 6A.2 / RETURN AUDIT" title="TDS Return Audit" subtitle="Compare return evidence with committed payments and frozen calculation, deposit and interest snapshots." /><section className="workspace-section">
    <label>Assignment<select disabled={busy} value={assignmentId} onChange={e => { setAssignmentId(e.target.value); setArtifactId(""); setCalculationId(""); setSelectedRun(""); setMessage(""); }}><option value="">Select assignment</option>{assignments.data?.map(a => <option key={a.assignment_id} value={a.assignment_id}>{a.assessee_legal_name} | FY {a.financial_year}</option>)}</select></label>
    {assignment && <div className="assignment-page-context"><b>{assignment.assessee_legal_name}</b><span>TAN {assignment.tan || "Not provided"}</span><span>FY {assignment.financial_year} · {assignment.quarter || "All quarters"}</span><span>{assignment.status}</span></div>}
    <label>Frozen calculation<select disabled={busy} value={calculationId} onChange={e => setCalculationId(e.target.value)}><option value="">Latest snapshot (review if unavailable)</option>{calculations.data?.map(c => <option key={c.calculation_id} value={c.calculation_id}>{c.calculation_id}</option>)}</select></label>
    <input ref={input} hidden type="file" accept=".zip,.fvu,.txt,.csv,.pdf" onChange={e => upload(e.target.files?.[0])} />
    <div className="wizard-actions"><button className="primary-button" disabled={busy || !assignmentId} onClick={() => input.current?.click()}>Upload return artifact</button><button className="secondary-button" disabled={busy || !artifactId || calculations.isError} onClick={run}>Run Return Audit</button></div>
    {message && <InfoNotice tone="info">{message}</InfoNotice>}{(artifacts.isError || results.isError || summary.isError || calculations.isError) && <InfoNotice tone="info">Evidence could not be loaded. Refresh before running an audit.</InfoNotice>}
    <div className="compact-list">{artifacts.data?.map(a => <button disabled={busy} className="compact-list-row" key={a.artifact_id} onClick={() => setArtifactId(a.artifact_id)}><div><b>{a.filename}</b><small>{a.return_form || "Form to review"} | {a.parser_status} | {a.artifact_id}</small></div><span>{artifactId === a.artifact_id ? "Selected" : "Select"}</span></button>)}</div>
    <label>Audit run<select value={runId || ""} onChange={e => setSelectedRun(e.target.value)}><option value="">Latest run</option>{Array.from(new Set((results.data || []).map(r => r.audit_run_id).filter(Boolean))).map(id => <option key={id} value={id}>{id}</option>)}</select></label>
    <p>Run: {runId || "No audit yet"} | Artifact: {rows[0]?.artifact_id || "Unavailable"} | {rows[0]?.statement_type || "Statement type unavailable"} | Original: {rows[0]?.original_return_reference || "Unavailable"} | Correction: {rows[0]?.correction_reference || "Unavailable"}</p>
    <div className="wizard-actions">{tabs.map(t => <button className={tab === t ? "primary-button" : "secondary-button"} key={t} onClick={() => setTab(t)}>{t}</button>)}</div><h2>{tab}</h2>
    {tab === "Return Summary" && summary.data && <table className="data-table"><tbody>{Object.entries(summary.data.summary).map(([key, amount]) => <tr key={key}><th>{key.split("_").join(" ")}</th><td>{show(amount)}</td></tr>)}</tbody></table>}
    <div className="table-scroll"><table className="data-table"><thead><tr><th>Return</th><th>Books / calculation</th><th>Deposit</th><th>Status / sources</th></tr></thead><tbody>{rows.filter(r => tab !== "Exceptions" || r.status !== "MATCHED").map(r => <tr key={r.result_id}>
      <td>{show(r.return?.deductee_name)}<br />PAN: {show(r.deductee_pan)}<br />Section: {show(r.return?.section)}<br />Date: {show(r.return?.payment_or_credit_date)}<br />Amount: {show(r.return?.amount_paid_or_credited)}<br />TDS: {show(r.return_tds_amount)}<p>Source: {show(r.return?.source_filename)} / row {show(r.return?.source_row_number)}</p></td>
      <td>Reference: {show(r.transaction_id)}<br />Amount: {show(r.books?.amount)}<br />Section: {show(r.calculation?.section_reference)}<br />Date: {show(r.calculation?.effective_event_date)}<br />Expected TDS: {show(r.expected_tds)}<br />Actual TDS: {show(r.actual_tds)}<br />TDS difference: {show(r.difference)}<p>Source: {show(r.books?.source_reference)} / {show(r.calculation?.calculation_id)}</p>{tab === "Payment vs Return" && Object.entries(r.payment_comparisons || {}).map(([field, status]) => <p key={field}>{field}: {status}</p>)}{tab === "Expected TDS vs Return TDS" && <p>Return minus actual TDS: {show(r.actual_tds_difference)}<br />Actual TDS comparison: {r.actual_tds_status}</p>}</td>
      <td>Challan: {(r.challan_comparisons || []).map(c => show(c.evidence.challan_number)).join(", ") || "Unavailable"}<br />Deposited: {show(r.deposit?.deposited_tds)}<br />{r.challan_status}{tab === "Challan/Deposit Comparison" && <p>Return tax deposited: {show(r.return_tax_deposited)}<br />Deposit tax difference: {show(r.deposit_tax_difference)}</p>}<details open={tab === "Challan/Deposit Comparison"}><summary>Challan references and comparison</summary>{(r.challan_comparisons || []).map((c, i) => <div key={i}><p>BSR: {show(c.return.bsr_code)} / {show(c.evidence.bsr_code)}<br />Date: {show(c.return.challan_deposit_date)} / {show(c.evidence.deposit_date)}<br />Amount: {show(c.return.challan_amount)} / {show(c.evidence.total_amount ?? c.evidence.amount_deposited)}</p>{Object.entries(c.comparisons).map(([field, status]) => <p key={field}>{field}: {status}</p>)}</div>)}</details>{tab === "Interest Evidence" && <p>Evidence available: {r.interest_evidence_available ? "Yes" : "No"}<br />{r.interest_status}<br />Frozen status: {show(r.interest?.overall_status)}<br />Deduction interest: {show(r.interest?.deduction_interest)}<br />Deposit interest: {show(r.interest?.deposit_interest)}<br />Existing difference: {show(r.interest_difference)}<br />Snapshot: {show(r.interest?.interest_run_id)}</p>}</td>
      <td><b>{r.status}</b><br />Identity: {r.identity_status}<br />Payment: {r.payment_status}<br />Tax: {r.tax_status}<br />Interest: {r.interest_status}<p>{r.reason}</p>{r.review_reasons?.length ? <><b>WHY REVIEW IS REQUIRED</b><ul>{r.review_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></> : null}<details><summary>Source references</summary><pre>{JSON.stringify(r.source_references, null, 2)}</pre></details>{r.status !== "MATCHED" && <button className="secondary-button" disabled={busy} onClick={() => setReviewResultId(r.result_id)}>Review this result</button>}</td>
    </tr>)}</tbody></table>{!rows.length && <p>No rows for this run.</p>}</div>
    {reviewResultId && <section className="workspace-section"><h2>CA Review Decision</h2><p>Selected result: {reviewResultId}. This records a separate immutable decision and does not alter return, ledger, calculation, deposit, or interest evidence.</p><label>Decision<select value={reviewDecision} onChange={e => setReviewDecision(e.target.value)}><option value="CONFIRM">CONFIRM</option><option value="REJECT">REJECT</option><option value="KEEP_REVIEW">KEEP_REVIEW</option><option value="MARK_UNSUPPORTED">MARK_UNSUPPORTED</option></select></label><label>Reason<textarea value={reviewReason} onChange={e => setReviewReason(e.target.value)} /></label><div className="wizard-actions"><button className="primary-button" disabled={busy} onClick={decide}>Record CA decision</button><button className="secondary-button" disabled={busy} onClick={() => setReviewResultId("")}>Cancel</button></div></section>}
    {decisions.data?.length ? <section className="workspace-section"><h2>Recorded CA Decisions</h2><div className="table-scroll"><table className="data-table"><thead><tr><th>Result</th><th>Decision</th><th>CA user</th><th>Reason</th><th>Timestamp</th></tr></thead><tbody>{decisions.data.map(d => <tr key={d.decision_id}><td>{d.audit_result_id}</td><td>{d.decision}</td><td>{d.user}</td><td>{d.reason}</td><td>{d.created_at}</td></tr>)}</tbody></table></div></section> : null}
  </section></>;
}
