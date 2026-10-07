import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, CloudUpload, Eye, RotateCcw, Search } from "lucide-react";
import { Amount, Drawer, EmptyState, ErrorNotice, Pagination, PageHeader, TableSkeleton } from "../components/common";
import { useWorkspace } from "../context/WorkspaceContext";
import { fmtDate, maskPan, title } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import { toast } from "sonner";
import type { SalesTds26asResult } from "../types";

const dash = "â€”";
const statusLabel = (value?: string | null) => value ? title(value) : dash;
const money = (value: number | null | undefined, signed = false) => <Amount value={value} signed={signed} />;
const identityAction = (row: SalesTds26asResult) => row.identity.status === "CONFIRMED" ? "Source relationship confirmed" : row.identity.status === "REVIEW_REQUIRED" ? "Review identity evidence" : row.identity.status === "CONFLICT" ? "Resolve conflicting evidence" : "Locate source relationship";

function Badge({ value }: { value?: string | null }) {
  const status = value || "NOT_DETERMINABLE";
  const green = status === "FULLY_RECONCILED" || status === "CONFIRMED" || status.endsWith("MATCHED");
  const red = status === "UNMAPPED" || status === "CONFLICT" || status.includes("EXCEPTION") || status.includes("DIFFERENCE");
  const label = status === "AMBIGUOUS_REVIEW" ? "Ambiguous Match ? Review Required" : status === "REVIEW_CANDIDATE" ? "Potential Match" : statusLabel(status);
  return <span className={`badge ${green ? "green" : red ? "red" : status.includes("REVIEW") ? "amber" : "muted"}`}>{label}</span>;
}

function uniqueRows(rows: Record<string, unknown>[] | undefined, id: string) {
  return Array.from(new Map((rows || []).map((row, index) => [String(row[id] || index), row])).values());
}

function sourceValue(field: string, value: unknown) {
  if (value === null || value === undefined || value === "") return dash;
  if (typeof value === "number") return money(value);
  if (Array.isArray(value)) return value.length ? value.map((item) => String(item)).join(", ") : dash;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(([, item]) => item !== null && item !== undefined && item !== "");
    return entries.length ? <span className="source-object">{entries.map(([key, item]) => <span key={key}><b>{statusLabel(key)}:</b> {typeof item === "number" ? money(item) : String(item)}</span>)}</span> : dash;
  }
  if ((field.includes("date") || field === "period") && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) return fmtDate(String(value));
  return String(value).replaceAll("_", " ");
}

function SourceRows({ title: heading, rows }: { title: string; rows?: Record<string, unknown>[] }) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const content = rows || [];
  const fields = useMemo(() => {
    const preferred = ["invoice_number", "invoice_date", "transaction_date", "customer_name", "party_name", "deductor_name", "customer_gstin", "tan", "invoice_value", "taxable_amount", "sales_amount", "amount_paid", "tds_expected", "tax_deducted", "tds_deposited", "reference", "state", "section", "status"];
    const present = new Set(content.flatMap((item) => Object.keys(item)));
    const selected = preferred.filter((field) => present.has(field));
    return selected.length ? selected.slice(0, 6) : Array.from(present).filter((field) => !["row_no", "tax_components"].includes(field)).slice(0, 6);
  }, [content]);
  const filtered = useMemo(() => content.filter((item) => JSON.stringify(item).toLowerCase().includes(query.trim().toLowerCase())), [content, query]);
  const openRow = expanded === null ? null : filtered[expanded];
  return <section className="sales-detail-block source-records">
    <div className="source-records-head"><div><span className="eyebrow">SOURCE EVIDENCE</span><h3>{heading}</h3><p>{content.length} linked record{content.length === 1 ? "" : "s"} Â· search by invoice, date, party, GSTIN, TAN, or amount.</p></div></div>
    {!content.length ? <p className="muted-copy">No source row is linked to this reconciliation relationship.</p> : <>
      <label className="source-record-search"><Search size={15} /><input value={query} onChange={(event) => { setQuery(event.target.value); setExpanded(null); }} placeholder="Search these source records" aria-label={`Search ${heading} records`} /><span>{filtered.length} of {content.length}</span></label>
      {!filtered.length ? <div className="source-record-empty">No linked records match this search.</div> : <div className="source-record-table-wrap"><table className="source-record-table"><thead><tr><th>Record</th>{fields.map((field) => <th key={field}>{statusLabel(field)}</th>)}<th aria-label="Open source record" /></tr></thead><tbody>{filtered.map((item, index) => <tr key={String(item.sales_transaction_id || item.tds_transaction_id || item.statement_id || index)} className={expanded === index ? "selected" : ""}><td><b>#{index + 1}</b><small>{String(item.sales_transaction_id || item.tds_transaction_id || item.statement_id || item.row_no || "Source row")}</small></td>{fields.map((field) => <td key={field}>{sourceValue(field, item[field])}</td>)}<td><button className="text-button source-row-button" onClick={() => setExpanded(expanded === index ? null : index)}>{expanded === index ? "Hide" : "View"}</button></td></tr>)}</tbody></table></div>}
      {openRow ? <details className="source-row-detail" open><summary>Full source record Â· {String(openRow.sales_transaction_id || openRow.tds_transaction_id || openRow.statement_id || openRow.row_no || "selected row")}</summary><dl>{Object.entries(openRow).filter(([, value]) => value !== null && value !== undefined && value !== "").map(([field, value]) => <div key={field}><dt>{statusLabel(field)}</dt><dd>{sourceValue(field, value)}</dd></div>)}</dl></details> : null}
    </>}
  </section>;
}
function friendlyIdentityMethod(method?: string | null) {
  const value = String(method || "").toUpperCase();
  if (value.startsWith("26AS_SOURCE")) return "26AS";
  if (value.includes("26AS") && value.includes("SALES")) return "26AS + Sales Match";
  if (value.includes("PAN")) return "PAN match";
  if (value.includes("GSTIN")) return "GSTIN match";
  if (value.includes("EXACT") || value.includes("NORMALIZED")) return "Source name match";
  if (value.includes("EMBEDDED") || value.includes("PARTIAL")) return "Sales candidate review";
  return "Source evidence review";
}

function identitySummary(identity: SalesTds26asResult["identity"]) {
  if (identity.status === "CONFIRMED") return "The relationship identity is confirmed from the available source evidence.";
  if (identity.status === "REVIEW_REQUIRED") return "The source names indicate a possible relationship, but CA confirmation is still required.";
  if (identity.status === "CONFLICT") return "The source identifiers conflict and must be resolved before the relationship can be accepted.";
  return "No supported Sales identity has been established for this 26AS deductor.";
}

function relationshipFinding(row: SalesTds26asResult) {
  const identity = row.identity || {} as SalesTds26asResult["identity"];
  const amount = row.amount_check || {} as SalesTds26asResult["amount_check"];
  const tds = row.tds_check || {} as SalesTds26asResult["tds_check"];
  const amountStatus = String(amount.status || "NOT_DETERMINABLE");
  const tdsStatus = String(tds.reconciliation_status || tds.status || "NOT_DETERMINABLE");
  const identityText = `Identity: ${identitySummary(identity)}`;
  const amountText = amountStatus.includes("MATCHED")
    ? "Sales amount: Sales taxable value agrees with the 26AS amount paid or credited."
    : amountStatus.includes("DIFFERENCE")
      ? `Sales amount: Sales taxable value and the 26AS amount differ by ${String(amount.difference == null ? "an undetermined amount" : `₹${new Intl.NumberFormat("en-IN").format(Math.abs(amount.difference))}`)}; review the linked invoices and 26AS entries.`
      : `Sales amount: ${amount.amount_match_reason || "the Sales-to-26AS amount relationship requires CA review."}`;
  const tdsText = tdsStatus.includes("MATCHED")
    ? "TDS: TDS receivable agrees with 26AS tax deducted."
    : tdsStatus.includes("DIFFERENCE")
      ? `TDS: TDS receivable and 26AS tax deducted differ by ${String(tds.tds_difference ?? tds.difference ?? "an undetermined amount")}; review the TDS ledger and 26AS evidence.`
      : `TDS: ${tds.review_reason || "the TDS relationship requires CA review."}`;
  return `${identityText} ${amountText} ${tdsText}`;
}

function relationshipAction(row: SalesTds26asResult) {
  const steps: string[] = [];
  if (row.identity?.status !== "CONFIRMED") steps.push("confirm the deductor-to-customer identity from source evidence");
  if (!String(row.amount_check?.status || "").includes("MATCHED")) steps.push("reconcile the Sales taxable value against the 26AS amount and supporting invoices");
  if (!String(row.tds_check?.reconciliation_status || row.tds_check?.status || "").includes("MATCHED")) steps.push("reconcile TDS receivable against 26AS tax deducted");
  return steps.length ? `CA should ${steps.join("; then ")}.` : row.recommended_action || "Retain the reviewed Sales, TDS ledger and 26AS evidence with the working papers.";
}

function Details({ row, onClose }: { row: SalesTds26asResult; onClose: () => void }) {
  const [showIdentityEvidence, setShowIdentityEvidence] = useState(false);
  // Historical completed runs may predate optional Sales evidence fields. Normalize them here so a missing field cannot crash the detail drawer.
  const identity = row.identity || {} as SalesTds26asResult["identity"];
  const salesCheck = row.sales_check || {} as SalesTds26asResult["sales_check"];
  const amountCheck = row.amount_check || {} as SalesTds26asResult["amount_check"];
  const tdsCheck = row.tds_check || {} as SalesTds26asResult["tds_check"];
  const sales = uniqueRows(amountCheck.sales_entries, "sales_transaction_id");
  const tds = uniqueRows(tdsCheck.tds_entries, "tds_transaction_id");
  const statements = uniqueRows(tdsCheck.statement_entries || amountCheck.statement_entries, "statement_id");
  const candidates = (salesCheck.candidate_customers || []).filter(Boolean);
  const hasSalesCheck = Boolean(row.sales_check);
  const identityExplanation = identity.review_reason || identity.reason || identitySummary(identity);
  const bridge = (row as SalesTds26asResult & { identity_bridge?: { statement?: Record<string, unknown>; ledger_candidates?: Record<string, unknown>[]; sales_candidates?: Record<string, unknown>[]; alias_evidence?: Record<string, unknown>[]; identity_decision?: Record<string, unknown> } }).identity_bridge;
  const ledgerCandidates = bridge?.ledger_candidates || [];
  const salesCandidates = bridge?.sales_candidates || [];
  const aliasEvidence = bridge?.alias_evidence || [];
  const selectedLedgerIds = Array.isArray(bridge?.identity_decision?.selected_ledger_groups) ? bridge.identity_decision.selected_ledger_groups.map(String) : [];
  const selectedLedgerNames = ledgerCandidates.filter((candidate) => selectedLedgerIds.includes(String(candidate.ledger_group_id))).map((candidate) => String(candidate.ledger_name || dash));
  const hasIdentityEvidence = Boolean(ledgerCandidates.length || salesCandidates.length || aliasEvidence.length);
  const transactionReconciliation = row.transaction_reconciliation;
  const transactionSummary = transactionReconciliation?.summary || {};
  const transactionRows = transactionReconciliation ? [
    ...transactionReconciliation.match_groups,
    ...transactionReconciliation.review_candidates,
    ...transactionReconciliation.unmatched_26as,
    ...transactionReconciliation.unmatched_sales,
  ] : [];
  return <Drawer open onClose={onClose} width={760} title="Reconciliation detail" subtitle={`${row.financial_year || dash} - ${statusLabel(row.overall_status)}`} testId="sales-tds-detail-drawer">
    <section className="review-flow" aria-label="CA review flow"><span className="eyebrow">CA REVIEW FLOW</span><ol><li><b>1</b><span><strong>Confirm identity</strong><small>Check that the 26AS deductor belongs to the Sales customer.</small></span></li><li><b>2</b><span><strong>Allocate transactions</strong><small>Review invoice-level matches, candidates and gaps.</small></span></li><li><b>3</b><span><strong>Check Sales amount</strong><small>Compare taxable value with 26AS amount paid or credited.</small></span></li><li><b>4</b><span><strong>Check TDS amount</strong><small>Compare TDS Receivable with 26AS tax deducted.</small></span></li><li><b>5</b><span><strong>Complete the action</strong><small>Use the recommended action before closing the review.</small></span></li></ol></section>
    <section className="sales-detail-block review-brief"><span className="eyebrow">STEP 1 - IDENTITY</span><span className="eyebrow">IDENTITY</span><div className="review-brief-title"><div><h3>{identity.status === "CONFIRMED" ? "Relationship identity is confirmed" : identity.status === "REVIEW_REQUIRED" ? "Identity confirmation is required" : "Source relationship needs attention"}</h3><p>{identitySummary(identity)}</p></div><Badge value={identity.status} /></div><div className="review-fact-grid"><div><span>26AS deductor</span><b>{identity.deductor_name || dash}</b><small className="mono">TAN {identity.tan || dash}</small></div><div><span>Sales customer</span><b>{identity.customer_name || "Customer not identified"}</b><small className="mono">GSTIN {identity.customer_gstin || dash}</small></div><div><span>Identity source</span><b>{friendlyIdentityMethod(identity.method)}</b><small>Confidence {identity.confidence == null ? dash : `${identity.confidence}%`}</small></div></div><div className="review-reason"><b>Identity assessment</b><p>{identityExplanation}</p></div>{hasIdentityEvidence ? <button type="button" className="secondary-button identity-evidence-toggle" onClick={() => setShowIdentityEvidence((visible) => !visible)}>{showIdentityEvidence ? "Hide identity evidence" : "View identity evidence"}</button> : <p className="identity-evidence-empty">No supporting identity evidence available.</p>}</section>
    {showIdentityEvidence && hasIdentityEvidence ? <section className="sales-detail-block identity-evidence-details"><span className="eyebrow">IDENTITY EVIDENCE</span><h3>26AS, Sales and ledger evidence</h3><div className="review-fact-grid"><div><span>26AS identity</span><b>{String(bridge?.statement?.deductor_name || identity.deductor_name || dash)}</b><small className="mono">TAN {String(bridge?.statement?.tan || identity.tan || dash)}</small></div><div><span>Identity method</span><b>{friendlyIdentityMethod(String(bridge?.identity_decision?.method || identity.method || ""))}</b><small>{String(bridge?.identity_decision?.reason || identityExplanation)}</small></div><div><span>Selected ledger groups</span><b>{selectedLedgerNames.length ? selectedLedgerNames.join(", ") : "None selected"}</b><small>Review-only candidates are not selected.</small></div></div>{aliasEvidence.length ? <div className="identity-evidence-list"><b>Alias evidence</b>{aliasEvidence.map((evidence, index) => <p key={`${String(evidence.alias)}-${index}`}><strong>{String(evidence.alias || dash)}</strong> links {String(evidence.canonical_name || dash)} to ledger {String(evidence.matched_ledger_name || dash)}.</p>)}</div> : null}<div className="bridge-candidates"><div><b>TDS Ledger candidates</b>{ledgerCandidates.map((candidate, index) => <p key={`${String(candidate.ledger_group_id)}-${index}`}><strong>{String(candidate.ledger_name || dash)}</strong> ? {money(Number(candidate.ledger_tds_amount))}</p>)}</div><div><b>Sales customer candidates</b>{salesCandidates.map((candidate, index) => <p key={`${String(candidate.customer_name)}-${index}`}><strong>{String(candidate.customer_name || dash)}</strong> ? {Number(candidate.sales_row_count ?? 0)} source rows ? Taxable Value {money(candidate.taxable_value as number | null)}</p>)}</div></div><p className="evidence-line"><b>Evidence control:</b> Amount equality supports reconciliation only; it does not confirm identity.</p></section> : null}
    {transactionReconciliation ? <section className="sales-detail-block transaction-reconciliation-block"><span className="eyebrow">STEP 2 - TRANSACTION ALLOCATION</span><h3>Match each 26AS transaction to a Sales invoice</h3><p className="transaction-intro">This is an invoice-level review. A review candidate is not allocated until a CA confirms the source evidence.</p><div className="review-fact-grid"><div><span>26AS transactions</span><b>{transactionSummary["26as_transaction_count"] ?? 0}</b><small>{money(transactionSummary["26as_amount_total"] as number | null)} Amount Paid/Credited</small></div><div><span>Sales invoices</span><b>{transactionSummary["sales_invoice_count"] ?? 0}</b><small>{money(transactionSummary["sales_taxable_value_total"] as number | null)} Taxable Value</small></div><div><span>Review candidates</span><b>{transactionSummary["review_candidate_count"] ?? 0}</b><small>{transactionSummary["unmatched_26as_count"] ?? 0} unmatched 26AS ? {transactionSummary["unmatched_sales_count"] ?? 0} unmatched Sales</small></div></div><ul className="transaction-reading-guide"><li><Badge value="MATCHED" /> The 26AS transaction and Sales invoice were allocated using controlled source evidence.</li><li><Badge value="REVIEW_CANDIDATE" /> The amount is a possible match, but a CA must confirm it before allocation.</li><li><Badge value="UNMATCHED_26AS" /> No supported Sales invoice was allocated to this 26AS transaction.</li><li><Badge value="UNMATCHED_SALES" /> This Sales invoice has not been allocated to a 26AS transaction.</li></ul><p className="transaction-row-count"><b>{transactionRows.length} review rows</b> - each row below shows one decision or one item requiring attention.</p><div className="source-record-table-wrap"><table className="source-record-table"><thead><tr><th>Status</th><th>26AS date</th><th>26AS amount</th><th>Sales invoice</th><th>Sales date</th><th>Sales Taxable Value</th><th>Difference</th><th>Match type</th></tr></thead><tbody>{transactionReconciliation.match_groups.map((group, index) => { const statementRows = (group["26as_rows"] || []) as Record<string, unknown>[]; const salesRows = (group["sales_rows"] || []) as Record<string, unknown>[]; return <tr key={String(group["match_group_id"] || index)}><td><Badge value={String(group["status"])} /></td><td>{sourceValue("transaction_date", statementRows[0]?.transaction_date)}</td><td>{money(group["26as_amount"] as number | null)}</td><td>{String(salesRows.map((item) => item.invoice_no || item.source_row_id).join(", ") || dash)}</td><td>{sourceValue("invoice_date", salesRows[0]?.invoice_date)}</td><td>{money(group["sales_taxable_value"] as number | null)}</td><td>{money(group["difference"] as number | null, true)}</td><td>{statusLabel(String(group["match_type"]))}</td></tr>; })}{transactionReconciliation.review_candidates.map((group, index) => { const statementRows = (group["26as_rows"] || []) as Record<string, unknown>[]; const salesRows = (group["sales_rows"] || []) as Record<string, unknown>[]; return <tr key={String(group["candidate_id"] || index)}><td><Badge value={String(group["status"])} /></td><td>{sourceValue("transaction_date", statementRows[0]?.transaction_date)}</td><td>{money(group["26as_amount"] as number | null)}</td><td>{String(salesRows.map((item) => item.invoice_no || item.source_row_id).join(", ") || dash)}</td><td>{sourceValue("invoice_date", salesRows[0]?.invoice_date)}</td><td>{money(group["sales_taxable_value"] as number | null)}</td><td>{money(group["difference"] as number | null, true)}</td><td>{statusLabel(String(group["match_type"]))}</td></tr>; })}{transactionReconciliation.unmatched_26as.map((item, index) => <tr key={`unmatched-26as-${index}`}><td><Badge value="UNMATCHED_26AS" /></td><td>{sourceValue("transaction_date", item.transaction_date)}</td><td>{money(item.amount_paid_credited as number | null)}</td><td>{dash}</td><td>{dash}</td><td>{dash}</td><td>{dash}</td><td>Unmatched 26AS</td></tr>)}{transactionReconciliation.unmatched_sales.map((item, index) => <tr key={`unmatched-sales-${index}`}><td><Badge value="UNMATCHED_SALES" /></td><td>{dash}</td><td>{dash}</td><td>{String(item.invoice_no || item.source_row_id || dash)}</td><td>{sourceValue("invoice_date", item.invoice_date)}</td><td>{money(item.taxable_value as number | null)}</td><td>{dash}</td><td>Unmatched Sales</td></tr>)}</tbody></table></div></section> : null}
    <section className="sales-detail-block sales-counterpart-brief"><span className="eyebrow">STEP 3 - SALES RECONCILIATION & EVIDENCE</span>{!hasSalesCheck ? <div className="historical-context"><b>This historical run has no dedicated Sales counterpart assessment.</b><p>Run a new Sales + TDS + 26AS reconciliation to create the current Sales identity, invoice totals and amount-basis assessment. The historical result remains unchanged.</p></div> : <><div className="review-fact-grid"><div><span>Sales customer</span><b>{salesCheck.customer_name || dash}</b><small className="mono">GSTIN {salesCheck.customer_gstin || dash}</small></div><div><span>Invoice evidence</span><b>{salesCheck.invoice_count ?? 0} linked invoice{salesCheck.invoice_count === 1 ? "" : "s"}</b><small>Invoice Value {money(salesCheck.invoice_value_total)} ? Taxable Value {money(salesCheck.taxable_value_total)}</small></div><div><span>Sales result</span><Badge value={salesCheck.amount_status} /><small>Basis: {salesCheck.amount_basis === "taxable_value" ? "Taxable Value" : statusLabel(salesCheck.amount_basis)}</small></div></div>{candidates.length ? <div className="candidate-list"><b>CA review candidates</b>{candidates.map((candidate) => <p key={`${candidate.customer_name}-${candidate.customer_gstin}`} className="muted-copy">{candidate.customer_name || dash} ? {candidate.customer_gstin || "GSTIN not provided"} ? {candidate.invoice_count} source invoice{candidate.invoice_count === 1 ? "" : "s"} ? Taxable Value {money(candidate.taxable_value_total)}</p>)}</div> : null}{salesCheck.review_reason ? <p className="evidence-line"><b>Sales review note:</b> {salesCheck.review_reason}</p> : null}</>}</section>
    <section className="sales-detail-block reconciliation-comparison"><span className="eyebrow">STEP 3 - SALES AMOUNT CONTROL</span><p className="muted-copy"><b>Comparison basis:</b> {amountCheck.sales_amount_basis_label || "Taxable Value"}</p><div><article><span>Sales Amount</span><b>{money(amountCheck.sales_amount)}</b><small>{amountCheck.sales_amount_basis_label || "Taxable Value"}</small></article><strong>VS</strong><article><span>26AS Amount Credited/Paid</span><b>{money(amountCheck.statement_amount_paid)}</b></article></div><p>Difference <b>{money(amountCheck.difference, true)}</b> <Badge value={amountCheck.status} /></p></section>
    <section className="sales-detail-block reconciliation-comparison"><span className="eyebrow">STEP 4 - TDS AMOUNT CONTROL</span><div><article><span>TDS Receivable</span><b>{money(tdsCheck.ledger_amount ?? tdsCheck.tds_book ?? tdsCheck.tds_expected)}</b></article><strong>VS</strong><article><span>26AS Tax Deducted</span><b>{money(tdsCheck.statement_amount ?? tdsCheck.tds_26as ?? tdsCheck.statement_tds_deducted)}</b></article></div><p>Difference <b>{money(tdsCheck.tds_difference ?? tdsCheck.difference, true)}</b> <Badge value={tdsCheck.reconciliation_status || tdsCheck.status} /></p>{tdsCheck.review_reason ? <p className="muted-copy"><b>Review reason:</b> {tdsCheck.review_reason}</p> : null}</section>
    <section className="sales-detail-block review-action"><span className="eyebrow">STEP 5 - RECOMMENDED CA ACTION</span><p><Badge value={row.overall_status} /> {row.reason}</p><p><b>Next action:</b> {row.recommended_action}</p></section>

    <SourceRows title="Sales Registry" rows={sales} />
    <SourceRows title="Sales Registry candidate evidence ? CA review required" rows={amountCheck.candidate_sales_entries} />
    <SourceRows title="TDS Expected / Receivable" rows={tds} />
    <SourceRows title="TDS Ledger identity candidates ? CA review required" rows={tdsCheck.candidate_tds_entries} />
    <SourceRows title={`26AS / Form 16A${statements.length > 1 ? ` ? ${statements.length} entries` : ""}`} rows={statements} />
  </Drawer>;
}

type TransactionFilter = "pending26as" | "noSalesCounterpart" | "pendingSales" | "no26asCounterpart" | "differences" | "all" | null;

const numberValue = (value: unknown) => typeof value === "number" ? value : Number(value || 0);
const sumRows = (rows: Record<string, unknown>[], fields: string[]) => rows.reduce((total, row) => total + numberValue(fields.map((field) => row[field]).find((value) => value !== null && value !== undefined)), 0);
const firstRows = (row: Record<string, unknown>, key: string) => Array.isArray(row[key]) ? row[key] as Record<string, unknown>[] : [];
const isAmbiguous = (row: Record<string, unknown>) => String(row.status || row.match_type || "").toUpperCase().includes("AMBIGUOUS");
const candidateSourceIds = (candidates: Record<string, unknown>[], side: "26as_rows" | "sales_rows") => new Set(candidates.flatMap((candidate) => firstRows(candidate, side).map((source) => String(source.source_row_id || "")).filter(Boolean)));
const hasCandidate = (row: Record<string, unknown>, ids: Set<string>) => ids.has(String(row.source_row_id || ""));

function CompactDetails({ row, onClose }: { row: SalesTds26asResult; onClose: () => void }) {
  const { runId, user } = useWorkspace();
  const [filter, setFilter] = useState<TransactionFilter>(null);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [showSources, setShowSources] = useState(false);
  const reconciliation = row.transaction_reconciliation;
  const unmatched26as = reconciliation?.unmatched_26as || [];
  const unmatchedSales = reconciliation?.unmatched_sales || [];
  const differenceGroups = (reconciliation?.match_groups || []).filter((group) => Math.abs(numberValue(group.difference)) > 0 || String(group.status || "").includes("DIFFERENCE"));
  const candidates = reconciliation?.review_candidates || [];
  const candidate26asIds = candidateSourceIds(candidates, "26as_rows");
  const candidateSalesIds = candidateSourceIds(candidates, "sales_rows");
  const pending26as = unmatched26as.filter((item) => hasCandidate(item, candidate26asIds));
  const noSalesCounterpart = unmatched26as.filter((item) => !hasCandidate(item, candidate26asIds));
  const pendingSales = unmatchedSales.filter((item) => hasCandidate(item, candidateSalesIds));
  const no26asCounterpart = unmatchedSales.filter((item) => !hasCandidate(item, candidateSalesIds));
  const pending26asCandidates = candidates.filter((candidate) => firstRows(candidate, "26as_rows").some((item) => hasCandidate(item, new Set(pending26as.map((row) => String(row.source_row_id || ""))))));
  const pendingSalesCandidates = candidates.filter((candidate) => firstRows(candidate, "sales_rows").some((item) => hasCandidate(item, new Set(pendingSales.map((row) => String(row.source_row_id || ""))))));
  const salesAmount = row.amount_check?.sales_amount;
  const statementAmount = row.amount_check?.statement_amount_paid;
  const difference = row.amount_check?.difference;
  const tdsReceivable = row.tds_check?.ledger_amount ?? row.tds_check?.tds_book ?? row.tds_check?.tds_expected;
  const statementTdsDeducted = row.tds_check?.statement_amount ?? row.tds_check?.tds_26as ?? row.tds_check?.statement_tds_deducted;
  const tdsDifference = row.tds_check?.tds_difference ?? row.tds_check?.difference;
  const tdsStatus = row.tds_check?.reconciliation_status || row.tds_check?.status;
  const show = (next: TransactionFilter) => { setFilter(next); setSelected(null); };
  const cards = [
    { key: "pending26as" as const, title: "Pending CA Review — 26AS", count: pending26as.length, amount: sumRows(pending26as, ["amount_paid_credited", "amount_paid", "amount"]), amountLabel: "Amount", action: "Review" },
    { key: "noSalesCounterpart" as const, title: "No Supported Sales Counterpart", count: noSalesCounterpart.length, amount: sumRows(noSalesCounterpart, ["amount_paid_credited", "amount_paid", "amount"]), amountLabel: "Amount", action: "View" },
    { key: "pendingSales" as const, title: "Pending CA Review — Sales", count: pendingSales.length, amount: sumRows(pendingSales, ["taxable_value", "taxable_amount", "sales_taxable_value", "amount"]), amountLabel: "Taxable Value", action: "Review" },
    { key: "no26asCounterpart" as const, title: "No Supported 26AS Counterpart", count: no26asCounterpart.length, amount: sumRows(no26asCounterpart, ["taxable_value", "taxable_amount", "sales_taxable_value", "amount"]), amountLabel: "Taxable Value", action: "View" },
    { key: "differences" as const, title: "Amount Differences", count: differenceGroups.length, amount: differenceGroups.reduce((total, group) => total + Math.abs(numberValue(group.difference)), 0), amountLabel: "Variance", action: "View" },
  ].filter((card) => card.count > 0);
  const detailRows = filter === "pending26as" ? pending26asCandidates : filter === "noSalesCounterpart" ? noSalesCounterpart : filter === "pendingSales" ? pendingSalesCandidates : filter === "no26asCounterpart" ? no26asCounterpart : filter === "differences" ? differenceGroups : [...(reconciliation?.match_groups || []), ...candidates, ...unmatched26as, ...unmatchedSales];
  const label = filter === "pending26as" ? "Pending CA Review — 26AS" : filter === "noSalesCounterpart" ? "No Supported Sales Counterpart" : filter === "pendingSales" ? "Pending CA Review — Sales" : filter === "no26asCounterpart" ? "No Supported 26AS Counterpart" : filter === "differences" ? "Amount Differences" : "Detailed Reconciliation";
  const preview = (rows: Record<string, unknown>[], kind: TransactionFilter, empty: string) => { const salesSide = kind === "pendingSales" || kind === "no26asCounterpart"; return rows.length ? <div className="difference-preview-list">{rows.slice(0, 3).map((item, index) => <button key={index} className="difference-preview-row" onClick={() => { show(kind); setSelected(item); }}><span>{salesSide ? String(item.invoice_no || item.invoice_number || "Sales invoice") : sourceValue("transaction_date", item.transaction_date || firstRows(item, "26as_rows")[0]?.transaction_date)}</span><b>{money(numberValue(salesSide ? item.taxable_value ?? item.taxable_amount : item.amount_paid_credited ?? item["26as_amount"]))}</b></button>)}</div> : <span className="difference-preview-empty">{empty}</span>; };
  return <Drawer open onClose={onClose} width={760} title="Sales vs 26AS" subtitle="" testId="sales-tds-detail-drawer">
    <section className="difference-header"><span className="eyebrow">RECONCILIATION DETAIL</span><h3>{row.identity?.deductor_name || "26AS Deductor"}</h3><small>TAN: <b className="mono">{row.identity?.tan || dash}</b></small><div className="comparison-heading"><span>Sales vs 26AS</span></div><div className="difference-amounts"><div><span>26AS Amount Paid / Credited</span><b>{money(statementAmount)}</b></div><strong>VS</strong><div><span>Sales Taxable Value</span><b>{money(salesAmount)}</b></div><div className="difference-total"><span>Difference</span><b>{money(difference, true)}</b></div></div><div className="comparison-heading tds-heading"><span>TDS reconciliation for this deductor</span><Badge value={tdsStatus} /></div><div className="difference-amounts tds-comparison"><div><span>TDS Receivable</span><b>{money(tdsReceivable)}</b><small>Ledger evidence for this deductor</small></div><strong>VS</strong><div><span>26AS TDS Deducted</span><b>{money(statementTdsDeducted)}</b><small>Credit reported by this TAN</small></div><div className="difference-total"><span>TDS Difference</span><b>{money(tdsDifference, true)}</b><small>Receivable less deducted</small></div></div></section>
    {runId && <SalesCommentary runId={runId} relationshipId={(row as SalesTds26asResult & { relationship_id?: string }).relationship_id || row.id} reviewer={user.name} />}
    {!reconciliation ? <section className="difference-empty"><b>Transaction-level detail is not available for this historical run.</b></section> : <>
      <section className="difference-summary"><span className="eyebrow">What makes the difference?</span><div className="difference-card-grid">{cards.map((card) => <article key={card.key} className="difference-card"><span>{card.title}</span><small>{card.count} {card.count === 1 ? "item" : "items"}</small>{card.amountLabel && <><b>{money(card.amount)}</b><em>{card.amountLabel}</em></>}<button className="secondary-button" onClick={() => show(card.key)}>{card.action}</button></article>)}</div>{!cards.length && <p className="difference-clear">All available transaction allocations are reconciled.</p>}</section>
      {(pending26as.length || noSalesCounterpart.length || pendingSales.length || no26asCounterpart.length || differenceGroups.length) && <section className="difference-previews"><div>{pending26as.length ? <><h3>Pending CA Review — 26AS</h3>{preview(pending26as, "pending26as", "")}</> : null}{noSalesCounterpart.length ? <><h3>No Supported Sales Counterpart</h3>{preview(noSalesCounterpart, "noSalesCounterpart", "")}</> : null}</div><div>{pendingSales.length ? <><h3>Pending CA Review — Sales</h3>{preview(pendingSales, "pendingSales", "")}</> : null}{no26asCounterpart.length ? <><h3>No Supported 26AS Counterpart</h3>{preview(no26asCounterpart, "no26asCounterpart", "")}</> : null}{differenceGroups.length ? <><h3>Amount Differences</h3><div className="difference-mini-table"><span>26AS</span><span>Sales</span><span>Difference</span>{differenceGroups.slice(0, 3).map((group, index) => <button key={index} onClick={() => { show("differences"); setSelected(group); }}><b>{money(numberValue(group["26as_amount"]))}</b><b>{money(numberValue(group.sales_taxable_value))}</b><b>{money(numberValue(group.difference), true)}</b></button>)}</div></> : null}</div></section>}
      <button className="secondary-button detailed-reconciliation-button" onClick={() => show(filter === "all" ? null : "all")}>{filter === "all" ? "Hide detailed reconciliation" : "View detailed reconciliation"}</button>
      {filter && <section className="difference-details"><div className="difference-details-head"><div><span className="eyebrow">{label}</span><h3>{detailRows.length} {detailRows.length === 1 ? "item" : "items"}</h3></div><button className="text-button" onClick={() => show(null)}>Close</button></div><div className="source-record-table-wrap"><table className="source-record-table difference-table"><thead><tr><th>Status</th><th>26AS date</th><th>26AS amount</th><th>Sales invoice</th><th>Sales date</th><th>Sales taxable value</th><th>Difference</th><th /></tr></thead><tbody>{detailRows.map((item, index) => { const statement = firstRows(item, "26as_rows")[0] || item; const sales = firstRows(item, "sales_rows")[0] || item; return <tr key={index}><td><Badge value={String(item.status || (filter === "noSalesCounterpart" ? "UNMATCHED_26AS" : filter === "no26asCounterpart" ? "UNMATCHED_SALES" : "REVIEW_CANDIDATE"))} /></td><td>{sourceValue("transaction_date", statement.transaction_date)}</td><td>{money(numberValue(item["26as_amount"] ?? statement.amount_paid_credited))}</td><td>{String(sales.invoice_no || sales.invoice_number || dash)}</td><td>{sourceValue("invoice_date", sales.invoice_date)}</td><td>{money(numberValue(item.sales_taxable_value ?? sales.taxable_value ?? sales.taxable_amount))}</td><td>{money(numberValue(item.difference), true)}</td><td><button className="text-button" onClick={() => setSelected(item)}>View</button></td></tr>; })}</tbody></table></div>{selected && <article className="difference-item-detail"><div><span className="eyebrow">{label}</span><h3>{String(selected.invoice_no || selected.invoice_number || sourceValue("transaction_date", selected.transaction_date) || "Selected item")}</h3></div><div className="review-fact-grid"><div><span>26AS amount</span><b>{money(numberValue(selected["26as_amount"] ?? selected.amount_paid_credited))}</b></div><div><span>Sales taxable value</span><b>{money(numberValue(selected.sales_taxable_value ?? selected.taxable_value ?? selected.taxable_amount))}</b></div><div><span>Difference</span><b>{money(numberValue(selected.difference), true)}</b></div></div></article>}</section>}
      <button className="text-button source-evidence-button" onClick={() => setShowSources((visible) => !visible)}>{showSources ? "Hide source evidence" : "View source evidence"}</button>
      {showSources && <><SourceRows title="Sales source records" rows={uniqueRows(row.amount_check?.sales_entries, "sales_transaction_id")} /><SourceRows title="26AS source records" rows={uniqueRows(row.tds_check?.statement_entries || row.amount_check?.statement_entries, "statement_id")} /></>}
    </>}
  </Drawer>;
}

function SalesCommentary({ runId, relationshipId, reviewer }: { runId: string; relationshipId: string; reviewer: string }) {
  const qc = useQueryClient(); const [text, setText] = useState(""); const [status, setStatus] = useState<"NEW" | "REVIEWED">("NEW");
  const current = useQuery({ queryKey: ["sales-commentary", runId, relationshipId], queryFn: () => api.relationshipCommentary(runId, relationshipId) });
  useEffect(() => { if (current.data?.commentary) { setText(current.data.commentary.commentary); setStatus(current.data.commentary.review_status); } }, [current.data?.commentary?.commentary_id]);
  const save = useMutation({ mutationFn: () => current.data?.commentary ? api.updateRelationshipCommentary(runId, relationshipId, { commentary: text, review_status: status, reviewer }) : api.createRelationshipCommentary(runId, relationshipId, { commentary: text, review_status: status, reviewer }), onSuccess: () => { toast.success("CA commentary saved"); qc.invalidateQueries({ queryKey: ["sales-commentary", runId, relationshipId] }); qc.invalidateQueries({ queryKey: ["sales-tds-results", runId] }); }, onError: e => toast.error(getErrorMessage(e, "Could not save CA commentary.")) });
  return <section className="sales-detail-block ca-commentary" data-testid="sales-relationship-commentary"><span className="eyebrow">CA COMMENTARY</span><h3>CA review conclusion</h3><p className="muted-copy">This working-paper commentary is separate from identity, Sales, TDS and matching outcomes.</p><textarea aria-label="CA commentary" value={text} maxLength={2000} placeholder="Record evidence reviewed, conclusion and follow-up." onChange={e => setText(e.target.value)} /><div className="commentary-actions"><label>Review status<select value={status} onChange={e => setStatus(e.target.value as "NEW" | "REVIEWED")}><option value="NEW">New</option><option value="REVIEWED">Reviewed</option></select></label><small>{text.length}/2000 characters</small><button className="primary-button" disabled={!text.trim() || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : current.data?.commentary ? "Save new revision" : "Save commentary"}</button></div>{current.data?.commentary && <p className="muted-copy">Revision {current.data.commentary.version} · last updated by {current.data.commentary.updated_by}.</p>}</section>;
}

type Filters = { search: string; overall: string; identity: string; amount: string; tds: string };
const EMPTY_FILTERS: Filters = { search: "", overall: "", identity: "", amount: "", tds: "" };

export default function SalesTds26asPage() {
  const navigate = useNavigate();
  const { runId, run, processing } = useWorkspace();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState<SalesTds26asResult | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const summary = useQuery({ queryKey: ["sales-tds-summary", runId], queryFn: () => api.salesTdsSummary(runId!), enabled: !!runId && run?.workflow === "SALES_TDS_26AS" && !processing });
  const results = useQuery({ queryKey: ["sales-tds-results", runId], queryFn: () => api.salesTdsResults(runId!), enabled: !!runId && run?.workflow === "SALES_TDS_26AS" && !processing });
  const rows = results.data?.items || [];
  useEffect(() => {
    const detailId = searchParams.get("detail");
    const identityFilter = searchParams.get("identity");
    if (identityFilter && identityFilter !== filters.identity) setFilters((current) => ({ ...current, identity: identityFilter }));
    if (!detailId) return;
    const target = rows.find((row) => row.id === detailId);
    if (target) setSelected(target);
  }, [rows, searchParams, filters.identity]);
  const filtered = useMemo(() => rows.filter((row) => {
    const haystack = [row.identity.customer_name, row.identity.deductor_name, row.identity.customer_pan, row.identity.tan].join(" ").toLowerCase();
    return (!filters.search || haystack.includes(filters.search.toLowerCase()))
      && (!filters.overall || row.overall_status === filters.overall)
      && (!filters.identity || row.identity.status === filters.identity)
      && (!filters.amount || row.amount_check.status === filters.amount)
      && (!filters.tds || (row.tds_check.reconciliation_status || row.tds_check.status) === filters.tds);
  }), [rows, filters]);
  const visible = filtered.slice((page - 1) * pageSize, page * pageSize);
  const s = summary.data?.summary || {};
  const primarySummary = s as Record<string, unknown>;
  const identity = (s.identity_counts || {}) as Record<string, number>;
  const tdsCounts = (primarySummary.tds_status_counts || {}) as Record<string, number>;
  const salesCounts = (primarySummary.sales_status_counts || {}) as Record<string, number>;
  const coverage = (primarySummary.sales_side_coverage || {}) as Record<string, number>;
  const cards: Array<[string, number, Partial<Filters>]> = [["26AS Deductors", Number(s.deductor_count || 0), {}], ["26AS Entries", Number(s.statement_count || 0), {}], ["Identity Confirmed", Number(identity.CONFIRMED || 0), { identity: "CONFIRMED" }], ["Identity Review Required", Number(identity.REVIEW_REQUIRED || 0), { identity: "REVIEW_REQUIRED" }], ["Identity Unmapped", Number(identity.UNMAPPED || 0), { identity: "UNMAPPED" }], ["Identity Conflicts", Number(identity.CONFLICT || 0), { identity: "CONFLICT" }], ["TDS Matched", Number(tdsCounts.MATCHED || 0), { tds: "TDS_MATCHED" }], ["TDS Differences", Number(tdsCounts.DIFFERENCE || 0), { tds: "TDS_DIFFERENCE" }], ["Missing Ledger", Number(tdsCounts.MISSING_TDS_LEDGER_COUNTERPART || 0), { tds: "MISSING_IN_TDS" }], ["Sales Found", Number(salesCounts.COUNTERPART_FOUND || 0), {}], ["Sales Missing", Number(salesCounts.MISSING_SALES_COUNTERPART || 0), {}], ["Sales Review", Number(salesCounts.REVIEW_REQUIRED || 0), {}], ["Amount Basis Pending", Number(primarySummary.amount_not_determinable_count || 0), { amount: "AMOUNT_NOT_DETERMINABLE" }]];
  const set = (patch: Partial<Filters>) => { setFilters((current) => ({ ...current, ...patch })); setPage(1); };
  const active = Object.values(filters).some(Boolean);
  if (run?.workflow !== "SALES_TDS_26AS") return null;
  return <>
    <PageHeader eyebrow="26AS-FIRST RECONCILIATION" title="Sales + TDS + 26AS" subtitle="Each primary relationship starts with a 26AS deductor. Sales and TDS Ledger records are supporting evidence, retained at their source granularity." action={<button className="secondary-button" data-testid="sales-new-reconciliation-button" onClick={() => navigate("/reconciliation/new")}><CloudUpload size={15} /> New Reconciliation</button>} />
    <section className="sales-run-context" data-testid="sales-run-context"><div><span>CLIENT / ASSESSEE</span><b>{run.assessee_name || dash}</b></div><div><span>PAN</span><b className="mono">{maskPan(run.assessee_pan || "")}</b></div><div><span>FINANCIAL YEAR</span><b>{run.financial_year || dash}</b></div><div><span>WORKFLOW</span><b>Sales + TDS + 26AS</b></div><div><span>RUN STATUS</span><b>{statusLabel(run.status)}</b></div></section>
    <section className="sales-summary-grid sales-summary-compact" data-testid="sales-summary-cards">{cards.map(([label, value, filter]) => <button key={label} className="sales-filter-card" onClick={() => set(filter)}><span>{label}</span><b>{value}</b></button>)}</section>
    <section className="sales-side-coverage" data-testid="sales-side-coverage"><div><span className="eyebrow">SALES-SIDE COVERAGE</span><b>{coverage.invoice_rows_excluded_from_primary_universe || 0} invoice rows are outside the primary 26AS deductor universe.</b><p>{coverage.customers_without_26as_counterpart || 0} Sales customers have no exact 26AS deductor counterpart in this run. They remain available as coverage evidence and do not create primary reconciliation rows.</p></div></section>
    <div className="filter-bar sales-filter-bar" data-testid="sales-workbench-filter-bar"><div className="search-field"><Search size={15} /><input aria-label="Search customer, deductor, PAN or TAN" placeholder="Search customer / deductor / PAN / TAN" value={filters.search} onChange={(event) => set({ search: event.target.value })} /></div><Filter label="Overall Status" value={filters.overall} options={["FULLY_RECONCILED", "AMOUNT_EXCEPTION", "TDS_EXCEPTION", "COMBINED_EXCEPTION", "IDENTITY_REVIEW_REQUIRED", "IDENTITY_CONFLICT", "UNMAPPED", "NOT_DETERMINABLE"]} onChange={(overall) => set({ overall })} /><Filter label="Identity" value={filters.identity} options={["CONFIRMED", "REVIEW_REQUIRED", "UNMAPPED", "CONFLICT"]} onChange={(identity) => set({ identity })} /><Filter label="Amount" value={filters.amount} options={["AMOUNT_MATCHED", "AMOUNT_DIFFERENCE", "MISSING_IN_26AS", "MISSING_IN_SALES", "AMOUNT_NOT_DETERMINABLE"]} onChange={(amount) => set({ amount })} /><Filter label="TDS" value={filters.tds} options={["TDS_MATCHED", "TDS_DIFFERENCE", "MISSING_IN_26AS", "MISSING_IN_TDS", "TDS_NOT_DETERMINABLE"]} onChange={(tds) => set({ tds })} /><button className="secondary-button" disabled={!active} onClick={() => { setFilters(EMPTY_FILTERS); setPage(1); }}><RotateCcw size={14} /> Clear Filters</button></div>
    <section className="workspace-section table-section" data-testid="sales-reconciliation-workbench"><div className="table-toolbar"><div className="table-meta"><b>{filtered.length} reconciliation relationship{filtered.length === 1 ? "" : "s"}</b><span>Every relationship shows its decision-making values together. Open a card only for source-level evidence.</span></div></div>{results.isLoading ? <TableSkeleton rows={8} cols={4} /> : results.isError ? <ErrorNotice message={getErrorMessage(results.error)} onRetry={() => results.refetch()} /> : !filtered.length ? <EmptyState title={active ? "No relationships match these filters" : "No reconciliation relationships yet"} text={active ? "Clear or adjust filters to view results." : "Complete the Sales + TDS + 26AS workflow to populate this workbench."} /> : <><div className="relationship-grid" data-testid="sales-relationship-grid">{visible.map((row) => <article key={row.id} className="relationship-card" data-testid={`sales-workbench-row-${row.id}`} tabIndex={0} onClick={() => setSelected(row)} onKeyDown={(event) => event.key === "Enter" && setSelected(row)}><header><div><div className="relationship-overall"><span>Overall</span><Badge value={row.overall_status} /></div><h3>{row.identity.customer_name || "Customer mapping required"}</h3><p className="relationship-deductor">Deductor: {row.identity.deductor_name || dash}</p></div><button className="icon-button" aria-label="View reconciliation detail" onClick={(event) => { event.stopPropagation(); setSelected(row); }}><Eye size={15} /><ChevronRight size={14} /></button></header><div className="relationship-identifiers"><span>TAN <b className="mono">{row.identity.tan || dash}</b></span><span>PAN <b className="mono">{maskPan(row.identity.customer_pan || "")}</b></span><span className="relationship-identity"><small>Identity</small><Badge value={row.identity.status} /></span></div><div className="relationship-check"><div className="relationship-check-title"><b>Amount reconciliation</b><Badge value={row.amount_check.status} /></div><div className="relationship-values"><span><small>Sales Amount ? {row.amount_check.sales_amount_basis_label || "Taxable Value"}</small><b>{money(row.amount_check.sales_amount)}</b></span><span><small>26AS Amount</small><b>{money(row.amount_check.statement_amount_paid)}</b></span><span><small>Difference</small><b>{money(row.amount_check.difference, true)}</b></span></div></div><div className="relationship-check"><div className="relationship-check-title"><b>TDS reconciliation</b><Badge value={row.tds_check.reconciliation_status || row.tds_check.status} /></div><div className="relationship-values"><span><small>TDS Receivable</small><b>{money(row.tds_check.ledger_amount ?? row.tds_check.tds_book ?? row.tds_check.tds_expected)}</b></span><span><small>26AS TDS</small><b>{money(row.tds_check.statement_amount ?? row.tds_check.tds_26as ?? row.tds_check.statement_tds_deducted)}</b></span><span><small>Difference</small><b>{money(row.tds_check.difference, true)}</b></span></div></div>{row.transaction_reconciliation ? <div className="relationship-transaction-summary"><span>Transaction allocation</span><b>{Number(row.transaction_reconciliation.summary["matched_group_count"] || 0)} matched</b><b>{Number(row.transaction_reconciliation.summary["review_candidate_count"] || 0)} for review</b><b>{Number(row.transaction_reconciliation.summary["unmatched_26as_count"] || 0)} unmatched 26AS</b></div> : null}<footer><section className="card-review-copy"><span>SYSTEM FINDING</span><p>{relationshipFinding(row)}</p></section><section className="card-review-copy action"><span>RECOMMENDED CA ACTION</span><p>{relationshipAction(row)}</p></section><div className="commentary-indicator"><b>{(row as SalesTds26asResult & { commentary_summary?: { exists: boolean } }).commentary_summary?.exists ? "CA commentary added" : "CA commentary not added"}</b><span>Open detail to record the CA conclusion</span></div></footer></article>)}</div><Pagination page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} /></>}</section>
    {selected && <CompactDetails row={selected} onClose={() => { setSelected(null); const next = new URLSearchParams(searchParams); next.delete("detail"); setSearchParams(next, { replace: true }); }} />}
  </>;
}

function Filter({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <select aria-label={label} value={value} className={value ? "active" : ""} onChange={(event) => onChange(event.target.value)}><option value="">{label}: All</option>{options.map((option) => <option key={option} value={option}>{statusLabel(option)}</option>)}</select>;
}



