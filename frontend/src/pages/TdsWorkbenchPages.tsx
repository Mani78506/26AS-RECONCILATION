import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpenCheck, FileBarChart, FileWarning, Landmark, ReceiptText, Search, TimerReset } from "lucide-react";
import { ErrorNotice, InfoNotice, PageHeader } from "../components/common";
import { api } from "../services/api";
import type { TdsComplianceAssignment, TdsComplianceTransaction } from "../types";

const title = (value?: string | null) => value ? value.replaceAll("_", " ") : "Unavailable";
const amount = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);

function AssignmentShell({ eyebrow, heading, subtitle, children }: { eyebrow: string; heading: string; subtitle: string; children: (assignment: TdsComplianceAssignment) => ReactNode }) {
  const [params, setParams] = useSearchParams();
  const assignments = useQuery({ queryKey: ["tds-compliance-assignments"], queryFn: api.tdsComplianceAssignments });
  const assignmentId = params.get("assignment") || assignments.data?.[0]?.assignment_id || "";
  const assignment = assignments.data?.find(item => item.assignment_id === assignmentId);
  const changeAssignment = (next: string) => setParams(next ? { assignment: next } : {});
  return <>
    <PageHeader eyebrow={eyebrow} title={heading} subtitle={subtitle} />
    {assignments.isLoading ? <InfoNotice tone="info">Loading assignment context…</InfoNotice> : assignments.isError ? <ErrorNotice message="Assignment context could not be loaded." /> : !assignment ? <InfoNotice tone="info">Create a TDS Compliance assignment before opening this work area.</InfoNotice> : <>
      <section className="assignment-workbench-header"><div className="assignment-workbench-context"><div><span>CLIENT</span><b>{assignment.assessee_legal_name}</b></div><div><span>PAN</span><b>{assignment.assessee_pan || "Not provided"}</b></div><div><span>TAN</span><b>{assignment.tan || "Not provided"}</b></div><div><span>FY / QUARTER</span><b>{assignment.financial_year} · {assignment.quarter || "All quarters"}</b></div><div><span>ASSIGNMENT STATUS</span><b>{title(assignment.status)}</b></div></div><label>Assignment<select value={assignmentId} onChange={event => changeAssignment(event.target.value)}>{assignments.data?.map(item => <option key={item.assignment_id} value={item.assignment_id}>{item.assessee_legal_name} · {item.financial_year} · {item.quarter || "All"}</option>)}</select></label></section>
      <nav className="assignment-progress" aria-label="Assignment workflow"><a href={`/tds-compliance/assignments/${assignment.assignment_id}/plan`}>Plan</a><a href={`/tds-compliance/ledger?assignment=${assignment.assignment_id}`}>Payment Ledger</a><a href={`/tds-compliance/calculation?assignment=${assignment.assignment_id}`}>Calculation</a><a href={`/tds-compliance/deposit?assignment=${assignment.assignment_id}`}>Deposit</a><a href={`/tds-compliance/interest?assignment=${assignment.assignment_id}`}>Interest</a><a href={`/tds-compliance/return-audit?assignment=${assignment.assignment_id}`}>Return Audit</a><a href={`/tds-compliance/review-queue?assignment=${assignment.assignment_id}`}>Review Queue</a></nav>
      {children(assignment)}
    </>}
  </>;
}

export function TdsLedgerPage() {
  const navigate = useNavigate();
  return <AssignmentShell eyebrow="ASSIGNMENT · SOURCE DATA" heading="Payment Ledger" subtitle="Upload, validate, review and commit payment-ledger evidence before calculation.">{assignment => <LedgerBody assignment={assignment} onOpenWorkflow={() => navigate(`/tds-compliance/assignments?assignment=${assignment.assignment_id}`)} />}</AssignmentShell>;
}
function LedgerBody({ assignment, onOpenWorkflow }: { assignment: TdsComplianceAssignment; onOpenWorkflow: () => void }) {
  const ledger = useQuery({ queryKey: ["tds-ledger", assignment.assignment_id], queryFn: () => api.tdsPaymentLedger(assignment.assignment_id) });
  const transactions = useQuery({ queryKey: ["tds-transactions", assignment.assignment_id], queryFn: () => api.tdsComplianceTransactions(assignment.assignment_id) });
  const version = ledger.data?.ledger_version as Record<string, unknown> | null | undefined;
  return <div className="workbench-grid"><section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">LEDGER CONTROL</span><h2>Current committed version</h2></div><BookOpenCheck size={20} /></div><div className="detail-fields compact"><Detail label="Version" value={String(version?.version || "Not committed")} /><Detail label="Ledger version ID" value={String(version?.ledger_version_id || "—")} /><Detail label="Rows" value={String((ledger.data?.items || []).length)} /><Detail label="Assignment" value={assignment.assignment_id} /></div><p className="muted-copy">The existing controlled workflow remains: Upload → Validate → Review → Commit → Calculate.</p><button className="primary-button" onClick={onOpenWorkflow}>Open upload and validation workflow <ArrowRight size={15} /></button></section><section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">REVIEW PREVIEW</span><h2>Current ledger transactions</h2></div></div><Table rows={transactions.data?.items || []} columns={["transaction_id", "deductee_name", "payment_date", "amount", "transaction_status"]} /></section></div>;
}

export function TdsCalculationWorkbenchPage() {
  return <AssignmentShell eyebrow="ASSIGNMENT · CALCULATION" heading="TDS Calculation" subtitle="Frozen calculation results, source provenance and controlled statutory-rule context.">{assignment => <CalculationBody assignment={assignment} />}</AssignmentShell>;
}
function CalculationBody({ assignment }: { assignment: TdsComplianceAssignment }) {
  const query = useQuery({ queryKey: ["tds-transactions", assignment.assignment_id], queryFn: () => api.tdsComplianceTransactions(assignment.assignment_id) });
  const [filter, setFilter] = useState("ALL"); const [selected, setSelected] = useState<TdsComplianceTransaction | null>(null);
  const items = useMemo(() => (query.data?.items || []).filter(item => filter === "ALL" || (filter === "REVIEW" ? item.transaction_status?.includes("REVIEW") || item.calculation_status === "REVIEW_REQUIRED" : filter === "DIFFERENCE" ? item.tds_deduction_difference !== 0 && item.tds_deduction_difference != null : filter === "RULE" ? item.calculation_status === "RULE_NOT_FOUND" : item.calculation_status === "CALCULATED")), [query.data, filter]);
  return <><section className="workspace-section"><div className="workbench-toolbar"><div><b>Calculation results</b><small>{query.data?.summary?.total_transactions ?? 0} transactions · calculation {query.data?.calculation?.calculation_id || "not available"}</small></div><div className="filter-tabs">{[["ALL","All"],["REVIEW","Review Required"],["CALCULATED","Calculated"],["DIFFERENCE","Difference"],["RULE","Rule Not Found"]].map(([key,label]) => <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>)}</div></div>{query.isError ? <ErrorNotice message="Calculation records could not be loaded." /> : <Table rows={items} columns={["deductee_name","payment_date","payment_nature","section_reference","amount","deductee_pan","expected_tds","tds_deducted","tds_deduction_difference","transaction_status"]} onSelect={row => setSelected(row as TdsComplianceTransaction)} />}</section>{selected && <DetailDrawer title={`Transaction ${selected.transaction_id}`} onClose={() => setSelected(null)} values={{ "Source transaction": selected.source_file_name ? `${selected.source_file_name} · row ${selected.source_row_number}` : selected.transaction_id, "Payment nature": `${selected.payment_nature || "—"} (${selected.payment_nature_source || "source unavailable"})`, "Governing act": selected.governing_act, "Section / provision": selected.section_reference, "Rule version": selected.rule_version, "Threshold": selected.threshold_status, "Rate": selected.applicable_rate == null ? null : `${selected.applicable_rate}%`, "Expected TDS": amount(selected.expected_tds), "Actual TDS": amount(selected.tds_deducted), "Difference": amount(selected.tds_deduction_difference), "Reason": selected.reason }} />}</>;
}

export function TdsInterestPage() {
  return <AssignmentShell eyebrow="ASSIGNMENT · INTEREST" heading="Interest & Delay" subtitle="Persisted interest evidence from the existing Phase 5 workflow.">{assignment => <InterestBody assignment={assignment} />}</AssignmentShell>;
}
function InterestBody({ assignment }: { assignment: TdsComplianceAssignment }) {
  const calculations = useQuery({ queryKey: ["tds-calculations", assignment.assignment_id], queryFn: () => api.tdsCalculations(assignment.assignment_id) });
  return <section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">PERSISTED EVIDENCE</span><h2>Interest and delay review</h2><p className="muted">Select the deposit work area to run the existing persisted interest workflow. No new interest calculation is introduced here.</p></div><TimerReset size={20} /></div>{calculations.data?.length ? <Table rows={calculations.data} columns={["calculation_id","ledger_version_id","created_at"]} /> : <InfoNotice tone="info">No frozen calculation snapshot is available yet. Complete the payment-ledger and calculation stages first.</InfoNotice>}</section>;
}

export function TdsReviewQueuePage() {
  return <AssignmentShell eyebrow="ASSIGNMENT · CA REVIEW" heading="Review Queue" subtitle="Centralized operational queue. Decisions stay in their original controlled evidence workflows.">{assignment => <ReviewBody assignment={assignment} />}</AssignmentShell>;
}
function ReviewBody({ assignment }: { assignment: TdsComplianceAssignment }) {
  const tx = useQuery({ queryKey: ["tds-transactions", assignment.assignment_id], queryFn: () => api.tdsComplianceTransactions(assignment.assignment_id) });
  const returns = useQuery({ queryKey: ["return-audit-results", assignment.assignment_id], queryFn: () => api.returnAuditResults(assignment.assignment_id) });
  const items = [...(tx.data?.items || []).filter(item => item.transaction_status?.includes("REVIEW") || item.calculation_status === "REVIEW_REQUIRED").map(item => ({ kind: "Calculation", id: item.transaction_id, status: item.transaction_status, reason: item.reason, source: `${item.source_file_name || "Ledger"} / ${item.source_row_number || "—"}` })), ...(returns.data || []).filter(item => item.status !== "MATCHED").map(item => ({ kind: "Return Audit", id: item.result_id, status: item.status, reason: item.review_reasons?.join(" ") || item.reason, source: `${item.artifact_id || "Artifact"} / ${item.return?.source_row_number || "—"}` }))];
  return <section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">OPEN REVIEWS</span><h2>{items.length} item{items.length === 1 ? "" : "s"} need attention</h2></div><FileWarning size={20} /></div><Table rows={items} columns={["kind","id","status","reason","source"]} /></section>;
}

export function TdsReportsPage() {
  const reports = ["Assignment Summary", "TDS Calculation Report", "Deposit Report", "Interest Report", "Return Audit Report", "Exception Report"];
  return <><PageHeader eyebrow="TDS COMPLIANCE · REPORTS" title="Reports" subtitle="Report availability is explicit. No unsupported export is generated." /><section className="report-grid">{reports.map(name => <article className="report-card" key={name}><FileBarChart size={20} /><h2>{name}</h2><p>Reserved report type. Export is not currently available in this workspace.</p><span className="badge slate">Not available</span></article>)}</section></>;
}

export function TdsAdministrationPage() {
  const mappings = useQuery({ queryKey: ["tds-classification-mappings"], queryFn: api.tdsClassificationMappings });
  const navigate = useNavigate();
  return <><PageHeader eyebrow="TDS COMPLIANCE · ADMINISTRATION" title="Administration" subtitle="Controlled rule and classification configuration is separated from assignment work." /><div className="workbench-grid"><section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">STATUTORY RULES</span><h2>Rule governance</h2><p className="muted">Financial year, section, lifecycle and effective dates are managed in the existing controlled rules workspace.</p></div><ReceiptText size={20} /></div><button className="primary-button" onClick={() => navigate("/tds-compliance/settings")}>Open statutory rules</button></section><section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">CLASSIFICATION MAPPINGS</span><h2>Active mappings</h2><p className="muted">Source evidence is retained with controlled payment-nature mappings.</p></div></div>{mappings.isError ? <ErrorNotice message="Classification mappings could not be loaded." /> : <Table rows={mappings.data || []} columns={["mapping_id","payment_nature","section_reference","active","updated_at"]} />}</section></div><section className="workspace-section"><span className="eyebrow">SETTINGS</span><h2>Organization and security configuration</h2><p className="muted-copy">Authentication and assignment access remain enforced by the existing backend security boundary.</p><button className="secondary-button" onClick={() => navigate("/tds-compliance/settings")}>Open settings</button></section></>;
}

function Table({ rows, columns, onSelect }: { rows: any[]; columns: string[]; onSelect?: (row: any) => void }) { return rows.length ? <div className="table-scroll"><table className="data-table"><thead><tr>{columns.map(column => <th key={column}>{title(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={row.transaction_id || row.result_id || row.id || index} onClick={() => onSelect?.(row)} className={onSelect ? "clickable-row" : ""}>{columns.map(column => <td key={column}>{typeof row[column] === "number" && /amount|tds|difference/.test(column) ? amount(row[column]) : title(String(row[column] ?? "—"))}</td>)}</tr>)}</tbody></table></div> : <div className="empty-state"><Search size={24} /><b>No records in this view</b><p>Complete the preceding controlled workflow stage or adjust the filter.</p></div>; }
function Detail({ label, value }: { label: string; value: unknown }) { return <><dt>{label}</dt><dd>{value == null || value === "" ? "Unavailable" : String(value)}</dd></>; }
function DetailDrawer({ title: heading, onClose, values }: { title: string; onClose: () => void; values: Record<string, unknown> }) { return <aside className="workbench-drawer" role="dialog" aria-label={heading}><div><span className="eyebrow">CALCULATION DETAIL</span><h2>{heading}</h2></div><button className="secondary-button" onClick={onClose}>Close</button><dl>{Object.entries(values).map(([label, value]) => <Detail key={label} label={label} value={value} />)}</dl></aside>; }
