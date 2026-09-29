import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, Check, CloudUpload, Database, FlaskConical, RefreshCw, ShieldCheck, Tags } from "lucide-react";
import { toast } from "sonner";
import { Amount, Drawer, EmptyState, ErrorNotice, PageHeader, ResultBadge, SeverityBadge, Skeleton, StatCard } from "../components/common";
import { useWorkspace } from "../context/WorkspaceContext";
import { fmtDateTime, inr, maskPan, num, pct, RESULT_LABELS, METHOD_LABELS } from "../lib/format";
import { api, getErrorMessage } from "../services/api";

export default function DashboardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { runId, runs, run, runLoading, runError, refreshRun, watchJob, processing, selectClient, selectRun } = useWorkspace();
  const q = useQuery({ queryKey: ["summary", runId], queryFn: () => api.summary(runId), enabled: !!runId });
  const sample = useMutation({ mutationFn: api.loadSample, onSuccess: (job) => { toast.info("Sample dataset loaded", { description: "Processing the labelled sample reconciliation-" }); watchJob(job.job_id); }, onError: (e) => toast.error(getErrorMessage(e)) });
  const s = q.data?.summary || run?.summary || null;
  // Sales + TDS + 26AS persists a different, deliberately source-specific
  // summary. Keep the shared dashboard shell and read its own control totals.
  const isSalesTds26as = (q.data?.workflow_mode || run?.workflow) === "SALES_TDS_26AS";
  const salesSummary = isSalesTds26as ? (s as Record<string, any> | null) : null;
  const salesControl = (salesSummary?.control_totals || {}) as Record<string, number>;
  const salesIdentity = (salesSummary?.identity_counts || {}) as Record<string, number>;
  const salesTdsStatuses = (salesSummary?.tds_status_counts || {}) as Record<string, number>;
  const salesAmountStatuses = (salesSummary?.sales_amount_status_counts || {}) as Record<string, number>;
  const salesResultCount = Number(salesSummary?.result_count || 0);
  const [differenceBreakdownOpen, setDifferenceBreakdownOpen] = useState(false);
  const salesResults = useQuery({ queryKey: ["dashboard-sales-tds-results", runId], queryFn: () => api.salesTdsResults(runId!), enabled: !!runId && isSalesTds26as });
  const salesDifferenceRows = [...(salesResults.data?.items || [])].sort((left, right) => Math.abs(Number(right.amount_check?.difference ?? 0)) - Math.abs(Number(left.amount_check?.difference ?? 0)));
  const salesTdsMatched = Number(salesTdsStatuses.MATCHED ?? salesSummary?.tds_matched_count ?? 0);
  const salesReviewCount = Number(salesTdsStatuses.REVIEW_REQUIRED ?? salesSummary?.review_candidate_count ?? 0);
  const salesMissingLedgerCount = Number(salesTdsStatuses.MISSING_TDS_LEDGER_COUNTERPART ?? salesSummary?.missing_tds_ledger_count ?? 0);
  const tdsRelationshipMatchPercentage = salesResultCount ? (salesTdsMatched / salesResultCount) * 100 : 0;
  const firstKnownAmount = (...values: Array<number | null | undefined>) => values.find((value) => typeof value === "number" && Number.isFinite(value));
  // Older completed Sales runs stored equivalent controls under earlier names.
  // Read them as a display fallback while the API derives the complete modern
  // control model; no reconciliation data is changed in the browser.
  const selectedSalesTotal = firstKnownAmount(salesControl.primary_sales_taxable_total);
  const selectedStatementTotal = firstKnownAmount(salesControl.primary_26as_amount_total);
  const selectedSalesDifference = firstKnownAmount(salesControl.sales_difference_for_primary_population, salesControl.sales_difference);
  const tdsEvidenceTotal = firstKnownAmount(salesControl.tds_amount_evidence_available, salesControl.eligible_ledger_tds_total, salesSummary?.tds_expected_total);
  const statementTdsTotal = firstKnownAmount(salesControl.total_tds_amount, salesControl.statement_tds_total, salesSummary?.statement_tds_deducted_total);
  const tdsAmountDifference = (tdsEvidenceTotal ?? 0) - (statementTdsTotal ?? 0);
  const tdsAmountReconciled = tdsEvidenceTotal != null && statementTdsTotal != null && Math.abs(tdsAmountDifference) < 0.005;
  const fullyReconciledCount = (salesResults.data?.items || []).filter((relationship) => {
    const transaction = relationship.transaction_reconciliation;
    const tdsMatched = relationship.tds_check?.reconciliation_status === "MATCHED" || relationship.tds_check?.status === "TDS_MATCHED";
    return relationship.identity?.status === "CONFIRMED"
      && relationship.amount_check?.status === "AMOUNT_MATCHED"
      && tdsMatched
      && !!transaction
      && !transaction.unmatched_26as.length
      && !transaction.unmatched_sales.length
      && !transaction.review_candidates.length
      && !transaction.match_groups.some((group) => Math.abs(Number(group.difference || 0)) > 0);
  }).length;
  const fullyReconciledPercentage = salesResultCount ? (fullyReconciledCount / salesResultCount) * 100 : 0;
  const matchedForRun = (candidate: any) => candidate?.workflow === "SALES_TDS_26AS"
    ? Number(candidate.summary?.tds_matched_count || 0)
    : candidate?.summary ? Number(candidate.summary.matched_claimable_count || 0) + Number(candidate.summary.matched_not_claimable_count || 0) : null;
  const attentionForRun = (candidate: any) => candidate?.workflow === "SALES_TDS_26AS"
    ? Math.max(0, Number(candidate.summary?.result_count || 0) - Number(candidate.summary?.tds_matched_count || 0))
    : candidate?.summary ? candidate.summary.exceptions_count ?? 0 : null;
  // Older runs and 26AS-only analyses intentionally have a different summary
  // shape. Never assume reconciliation-only breakdown objects are present.
  const resultCounts = s?.result_counts && typeof s.result_counts === "object" ? s.result_counts : {};
  const methodCounts = s?.match_method_counts && typeof s.match_method_counts === "object" ? s.match_method_counts : {};
  const loading = runLoading || (q.isLoading && !!runId);
  const noRun = !runLoading && !runError && !runId;
  const showClientDirectory = searchParams.get("directory") === "1";
  const clients = useQuery({ queryKey: ["workspace-clients"], queryFn: api.clients, enabled: noRun || showClientDirectory, staleTime: 15_000 });
  const openClient = (clientId: string) => window.dispatchEvent(new CustomEvent("open-client-selector", { detail: { clientId } }));
  const startCurrentClientReconciliation = () => navigate("/reconciliation/new");
  const startNewClientReconciliation = () => {
    // This action is available from the client directory after Change client.
    // It must discard that prior selection before a new client is entered.
    selectRun(null);
    selectClient(null);
    navigate("/reconciliation/new?new_client=1");
  };

  if (noRun || showClientDirectory) return <>
    <PageHeader eyebrow="CLIENT DIRECTORY" title="26AS Reconciliation" subtitle="Choose a client to open a completed reconciliation or start a new reconciliation for that client." action={<button className="primary-button" onClick={() => window.dispatchEvent(new Event("open-client-selector"))}><Database size={16} /> Browse all clients</button>} />
    <section className="workspace-section client-directory" data-testid="workspace-client-directory"><div className="section-heading"><div><span className="eyebrow">CLIENTS</span><h2>Choose a client</h2></div><button className="secondary-button" onClick={startNewClientReconciliation}><CloudUpload size={15} /> New client reconciliation</button></div>
      {clients.isLoading ? <div className="client-directory-grid" aria-label="Loading clients">{[0, 1, 2].map((item) => <article className="client-directory-card loading" key={item}><Skeleton h={18} w="55%" /><Skeleton h={12} w="38%" /><Skeleton h={12} w="75%" /></article>)}</div>
        : clients.isError ? <ErrorNotice message="Client directory is unavailable. Check the backend connection and try again." onRetry={() => clients.refetch()} />
          : clients.data?.length ? <div className="client-directory-grid">{clients.data.map((client) => <article className="client-directory-card" key={client.client_id} data-testid={`client-directory-${client.client_id}`}><div><span className="eyebrow">CLIENT</span><h3>{client.client_name}</h3><p className="mono">PAN {maskPan(client.assessee_pan)}</p></div><div className="client-directory-meta"><span>{client.financial_years.join(" - ") || "FY not recorded"}</span><span>{client.completed_run_count} completed reconciliation{client.completed_run_count === 1 ? "" : "s"}</span></div><button className="primary-button" onClick={() => openClient(client.client_id)}>{client.completed_run_count ? "Open client" : "Start reconciliation"} <ArrowRight size={14} /></button></article>)}</div>
            : <div className="workspace-empty compact"><Database size={28} /><h2>No clients yet</h2><p>Start the first reconciliation to create a client workspace.</p><button className="primary-button" onClick={startNewClientReconciliation}><CloudUpload size={15} /> Start reconciliation</button></div>}
    </section>
  </>;

  return <>
    <PageHeader eyebrow="OVERVIEW" title="Dashboard" subtitle={isSalesTds26as ? "Sales, TDS Receivable and 26AS reconciliation status for the selected assessee." : "Reconciliation status for the selected assessee, control totals and items needing CA attention."} action={<><button className="secondary-button" data-testid="load-sample-button" disabled={sample.isPending || processing} onClick={() => sample.mutate()}><FlaskConical size={15} /> {sample.isPending ? "Loading..." : "Load sample dataset"}</button><button className="primary-button" data-testid="new-reconciliation-button" onClick={startCurrentClientReconciliation}><CloudUpload size={16} /> New Reconciliation</button></>} />
    {runError && <ErrorNotice message="Reconciliation service is unavailable. Check the backend connection and try again." onRetry={refreshRun} />}
    {q.isError && <ErrorNotice message={getErrorMessage(q.error)} onRetry={() => q.refetch()} />}
    {run?.status === "FAILED" && <ErrorNotice testId="run-failed-notice" message={`Last run failed: ${run.error || "unknown error"}`} onRetry={() => navigate("/reconciliation/new")} />}
    {run?.source === "sample" && <div className="sample-banner" data-testid="sample-banner"><FlaskConical size={14} /><span>You are viewing the <b>labelled sample dataset</b> (Meridian Consulting LLP, FY 2024-25). Upload real Books and 26AS files for a live reconciliation.</span></div>}

    <div className="context-strip" data-testid="context-strip">
      <div><span>ASSESSEE</span><b data-testid="context-assessee">{loading ? <Skeleton h={14} w={120} /> : run?.assessee_name || "Not selected"}</b></div>
      <div><span>ASSESSEE PAN</span><b className="mono" data-testid="context-pan">{loading ? <Skeleton h={14} w={90} /> : maskPan(run?.assessee_pan) }</b></div>
      <div><span>FINANCIAL YEAR</span><b data-testid="context-fy">{loading ? <Skeleton h={14} w={60} /> : run?.financial_year || "-"}</b></div>
      <div><span>QUARTERS</span><b data-testid="context-quarters">{loading ? <Skeleton h={14} w={80} /> : s?.by_quarter?.filter((x) => x.quarter !== "-").map((x) => x.quarter).join(" - ") || "-"}</b></div>
      <div><span>LAST RECONCILIATION</span><b data-testid="context-last-run">{loading ? <Skeleton h={14} w={120} /> : fmtDateTime(run?.finished_at || run?.created_at)}</b></div>
      <div className={`processing-state ${run?.status === "FAILED" ? "failed" : run?.status === "PROCESSING" ? "running" : ""}`} data-testid="context-status"><span className="status-dot" /> {run ? (run.status === "PROCESSING" ? "Processing..." : run.status === "FAILED" ? "Failed" : `Completed - ${run.parent_run_id ? "Rerun / New Version" : "Original Run"}`) : "Ready for source files"}</div>
    </div>

    <section className="stats-grid" data-testid="stats-grid">
      {isSalesTds26as ? <>
        <StatCard label="Fully Reconciled" value={`${fullyReconciledCount} / ${salesResultCount}`} detail={`${pct(fullyReconciledPercentage)} · Identity, TDS and Sales reconciliation are all resolved`} icon={ShieldCheck} tone="green" loading={loading} onClick={() => navigate("/reconciliation")} />
        <StatCard label="TDS Amount Reconciled" value={tdsAmountReconciled ? "100%" : "Review Required"} detail={`${inr(tdsEvidenceTotal ?? 0)} = ${inr(statementTdsTotal ?? 0)} · Difference ${inr(tdsAmountDifference)}`} icon={Check} tone={tdsAmountReconciled ? "green" : "amber"} loading={loading} onClick={() => navigate("/reconciliation")} />
        <StatCard label="TDS Relationships Matched" value={`${salesTdsMatched} / ${salesResultCount}`} detail={`${pct(tdsRelationshipMatchPercentage)} · TDS evidence matched for ${salesTdsMatched} of ${salesResultCount} relationships`} icon={ShieldCheck} tone="blue" loading={loading} onClick={() => navigate("/reconciliation")} />
        <StatCard label="Sales vs 26AS" value="Review Required" detail="Transaction/customer differences remain" icon={AlertCircle} tone="amber" loading={loading} onClick={() => setDifferenceBreakdownOpen(true)} />
        <StatCard label="26AS Deductors" value={num(salesSummary?.deductor_count ?? 0)} detail={`Supporting count · Sales rows: ${num(salesSummary?.sales_count ?? 0)}`} icon={Database} tone="slate" loading={loading} onClick={() => navigate("/reconciliation")} />
      </> : <>
        <StatCard label="Reconciliation" value={pct(s?.reconciliation_percentage ?? 0)} detail="Books TDS Expected matched to 26AS TDS Deducted" icon={ShieldCheck} tone="blue" loading={loading} />
        <StatCard label="Books transactions" value={num(s?.books_count ?? 0)} detail={`26AS entries: ${num(s?.statement_count ?? 0)}`} icon={Database} tone="slate" loading={loading} />
        <StatCard label="Matched & Claimable" value={num(s?.matched_claimable_count ?? 0)} detail={inr(s?.matched_claimable_amount ?? 0)} icon={Check} tone="green" loading={loading} onClick={() => navigate("/reconciliation?result=MATCHED_CLAIMABLE")} />
        <StatCard label="Matched - Not Claimable" value={num(s?.matched_not_claimable_count ?? 0)} detail={inr(s?.matched_not_claimable_amount ?? 0)} icon={ShieldCheck} tone="amber" loading={loading} onClick={() => navigate("/reconciliation?result=MATCHED_NOT_CLAIMABLE")} />
        <StatCard label="Exceptions" value={num(s?.exceptions_count ?? 0)} detail={`${s?.severity_counts?.HIGH ?? 0} high severity`} icon={AlertCircle} tone="red" loading={loading} onClick={() => navigate("/exceptions")} />
        <StatCard label="Identity Review" value={num(s?.identity_review_count ?? 0)} detail="TANs awaiting decision" icon={Tags} tone="purple" loading={loading} onClick={() => navigate("/identity-review")} />
      </>}
    </section>
    <div className="dashboard-grid">
      <section className="workspace-section" data-testid="control-totals">
        <div className="section-heading"><div><span className="eyebrow">RECONCILIATION OUTCOME</span><h2>What needs your attention</h2></div><button className="icon-button" data-testid="refresh-dashboard-button" aria-label="Refresh dashboard" onClick={() => { refreshRun(); q.refetch(); }}><RefreshCw size={15} /></button></div>
        {s ? isSalesTds26as ? <div className="sales-dashboard-control" data-testid="sales-control-totals">
          <div className="sales-control-overview">
            <article className="sales-control-card tds"><span className="eyebrow">TDS AMOUNT RECONCILED</span><h3>{tdsAmountReconciled ? "TDS amount evidence accounted for" : "TDS amount requires review"}</h3><div className="sales-control-values"><div><small>26AS TDS Deducted</small><Amount value={statementTdsTotal} strong /></div><strong>=</strong><div><small>TDS Receivable Evidence</small><Amount value={tdsEvidenceTotal} strong /></div></div><p><b>Difference:</b> <Amount value={tdsAmountDifference} signed strong /></p><p className="muted-copy">{tdsAmountReconciled ? "100% of TDS amount evidence accounted for." : "TDS amount evidence is not fully accounted for."} TDS relationship matching remains a separate measure.</p><div className="sales-control-status"><span className="badge blue">{salesTdsMatched} of {salesResultCount} TDS relationships matched</span><span className="badge amber">{salesReviewCount} relationships need review</span>{salesMissingLedgerCount > 0 && <span className="badge red">{salesMissingLedgerCount} missing ledger</span>}</div></article>
            <article className="sales-control-card sales"><span className="eyebrow">SALES VS 26AS</span><h3>Review Required</h3><span className="sr-only">Sales compared on Taxable Value</span><div className="sales-control-values"><div><small>Sales Taxable Value</small><Amount value={selectedSalesTotal} strong /></div><strong>Compared with</strong><div><small>26AS Amount Paid/Credited</small><Amount value={selectedStatementTotal} strong /></div></div><p><b>Difference:</b> <Amount value={selectedSalesDifference} signed strong /></p><p className="muted-copy">Transaction/customer differences remain in the selected 26AS relationship population.</p><button className="primary-button sales-difference-button" onClick={() => setDifferenceBreakdownOpen(true)}>View Difference Breakdown <ArrowRight size={14} /></button></article>
          </div>

        </div> : <div className="control-table"><Row label="Books TDS Expected" value={s.books_tds_expected} /><Row label="26AS Tax Deducted" value={s.statement_tax_deducted} /><Row label="26AS TDS Deposited" value={s.statement_tds_deposited} /><Row label="Matched (Books)" value={s.matched_amount} strong /><Row label="Difference (Books - 26AS)" value={s.difference} signed strong /><div className="control-row"><span>26AS Coverage</span><b data-testid="control-coverage">{pct(s.coverage_percentage)}</b></div></div>
          : loading ? <div className="control-table">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="control-row"><Skeleton h={12} w={140} /><Skeleton h={12} w={90} /></div>)}</div>
          : <EmptyState title="No control totals yet" text="Totals appear after the first reconciliation run." testId="control-totals-empty" />}
      </section>
      <section className="workspace-section" data-testid="attention-required">
        <div className="section-heading"><div><span className="eyebrow">NEXT BEST ACTION</span><h2>Attention required</h2></div>{s && (isSalesTds26as ? salesReviewCount > 0 : s.exceptions_count > 0) && <button className="text-button" data-testid="view-exceptions-button" onClick={() => navigate(isSalesTds26as ? "/reconciliation" : "/exceptions")}>View all <ArrowRight size={13} /></button>}</div>
        {loading ? <div className="control-table">{[0, 1, 2].map((i) => <div key={i} className="control-row"><Skeleton h={12} w="70%" /><Skeleton h={12} w={60} /></div>)}</div>
          : q.data?.attention?.length ? <ul className="attention-list">{q.data.attention.map((r: any) => <li key={r.id}><button data-testid={`attention-item-${r.transaction_id}`} onClick={() => navigate(isSalesTds26as ? `/reconciliation?detail=${encodeURIComponent(r.id)}` : `/reconciliation/${encodeURIComponent(r.id)}`)}><SeverityBadge value={r.severity} /><div><b>{r.transaction_id} ? {isSalesTds26as ? `26AS Deductor: ${r.deductor_name || "Unmapped"}` : r.customer ? `Books Customer: ${r.customer}` : `Deductor: ${r.deductor_name || "Unmapped"}`}</b><small>{isSalesTds26as ? `${String(r.identity_status || "Identity review").replaceAll("_", " ")} | ${String(r.amount_status || "Amount review").replaceAll("_", " ")} | ${String(r.tds_status || "TDS review").replaceAll("_", " ")}` : RESULT_LABELS[r.result]}{r.tan ? ` | TAN ${r.tan}` : ""}</small></div><Amount value={r.difference} signed /></button></li>)}</ul>
          : noRun ? <EmptyState title="Upload Books and 26AS to begin" text="Attention items appear here once a reconciliation has been processed." action={<button className="secondary-button" data-testid="start-from-empty-button" onClick={() => navigate("/reconciliation/new")}>Start reconciliation</button>} />
          : <EmptyState icon={Check} title="No exceptions require attention" text={isSalesTds26as ? "Every 26AS relationship has matched Sales and TDS evidence in the current run." : "Every books transaction is matched and claimable in the current run."} />}
      </section>
    </div>
    {differenceBreakdownOpen && <Drawer open onClose={() => setDifferenceBreakdownOpen(false)} width={920} title="Sales vs 26AS - Difference Breakdown" subtitle={`${salesDifferenceRows.length} 26AS-primary relationships, ordered by absolute financial difference.`} testId="sales-difference-breakdown"><section className="difference-breakdown"><div className="difference-breakdown-intro"><span className="eyebrow">CURRENT RELATIONSHIP POPULATION</span><h3>{salesDifferenceRows.length} 26AS-primary relationships</h3><p>Sales-only customers are excluded. Open a relationship to review its underlying 26AS transactions and Sales invoices.</p></div>{salesResults.isLoading ? <div className="control-table">{[1, 2, 3].map((item) => <div className="control-row" key={item}><Skeleton h={14} w="60%" /><Skeleton h={14} w={90} /></div>)}</div> : salesDifferenceRows.length ? <div className="difference-breakdown-list">{salesDifferenceRows.map((relationship) => <article key={relationship.id} className="difference-breakdown-row"><header><div><span className="eyebrow">RELATIONSHIP</span><h3>{relationship.identity.customer_name || relationship.identity.deductor_name || "Customer mapping required"}</h3><p>26AS Deductor: {relationship.identity.deductor_name || "Not provided"} - TAN <b className="mono">{relationship.identity.tan || "Not provided"}</b></p></div><button className="primary-button" onClick={() => { setDifferenceBreakdownOpen(false); navigate(`/reconciliation?detail=${encodeURIComponent(relationship.id)}`); }}>View transactions <ArrowRight size={14} /></button></header><div className="difference-breakdown-values"><div><span>Identity</span><ResultBadge result={relationship.identity.status} /></div><div><span>Sales Taxable Value</span><Amount value={relationship.amount_check.sales_amount} strong /></div><div><span>26AS Amount Paid/Credited</span><Amount value={relationship.amount_check.statement_amount_paid} strong /></div><div><span>Difference</span><Amount value={relationship.amount_check.difference} signed strong /></div><div><span>Sales status</span><ResultBadge result={relationship.amount_check.status} /></div><div><span>TDS status</span><ResultBadge result={relationship.tds_check.reconciliation_status || relationship.tds_check.status} /></div></div><p className="difference-breakdown-action"><b>CA action:</b> Review the relationship and its transaction-level evidence.</p></article>)}</div> : <EmptyState title="No relationship differences available" text="Complete a Sales + TDS + 26AS reconciliation to populate this breakdown." />}</section></Drawer>}
    <div className="dashboard-grid secondary">
      <section className="workspace-section" data-testid="result-breakdown">
        <div className="section-heading"><div><span className="eyebrow">OUTCOME MIX</span><h2>{isSalesTds26as ? "Sales and TDS outcomes" : "Results by type"}</h2></div></div>
        {s ? isSalesTds26as ? <div className="breakdown">{Object.entries({ ...salesAmountStatuses, ...Object.fromEntries(Object.entries(salesTdsStatuses).map(([key, value]) => [`TDS ${key}`, value])) }).map(([key, value]) => <button key={key} className="breakdown-row" data-testid={`breakdown-${key}`} onClick={() => navigate("/reconciliation")}><ResultBadge result={key} /><div className="bar"><i style={{ width: `${salesResultCount ? (Number(value) / salesResultCount) * 100 : 0}%` }} /></div><b>{String(value)}</b></button>)}</div> : <div className="breakdown">{Object.entries(resultCounts).map(([key, value]) => <button key={key} className="breakdown-row" data-testid={`breakdown-${key}`} onClick={() => navigate(`/reconciliation?result=${key}`)}><ResultBadge result={key} /><div className="bar"><i style={{ width: `${s.result_count ? (Number(value) / s.result_count) * 100 : 0}%` }} /></div><b>{String(value)}</b></button>)}{Object.keys(resultCounts).length === 0 && <p className="muted-copy">This 26AS-only analysis does not use reconciliation match categories.</p>}<div className="method-mix">{Object.entries(methodCounts).map(([key, value]) => <span key={key}><small>{METHOD_LABELS[key] || key}</small><b>{String(value)}</b></span>)}</div></div> : <EmptyState title="No reconciliation has been processed yet" text="Outcome mix appears after processing." testId="breakdown-empty" />}
      </section>      <section className="workspace-section" data-testid="recent-runs">
        <div className="section-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h2>Recent reconciliation</h2></div>{run && <button className="secondary-button" data-testid="open-reconciliation-button" onClick={() => navigate("/reconciliation")}>Open reconciliation <ArrowRight size={14} /></button>}</div>
        {runs.length ? <table className="mini-table"><thead><tr><th>Assessee</th><th>FY</th><th>Processed</th><th>Status</th><th className="num">Matched</th><th className="num">Attention</th></tr></thead><tbody>{runs.slice(0, 6).map((r) => <tr key={r.run_id} className={r.run_id === runId ? "current" : ""} data-testid={`recent-run-${r.run_id}`}><td>{r.assessee_name || "-"}{r.source === "sample" && <em className="sample-tag">sample</em>}</td><td>{r.financial_year || "-"}</td><td>{fmtDateTime(r.finished_at || r.created_at)}</td><td><span className={`badge ${r.status === "COMPLETED" ? "green" : r.status === "FAILED" ? "red" : "amber"}`}>{r.status}</span></td><td className="num">{matchedForRun(r) ?? "-"}</td><td className="num">{attentionForRun(r) ?? "-"}</td></tr>)}</tbody></table>
          : <EmptyState title="No reconciliation has been processed yet" text="Completed runs appear here with control totals and review status." testId="recent-runs-empty" />}
      </section>
    </div>
  </>;
}

function Row({ label, value, signed, strong }: { label: string; value: number; signed?: boolean; strong?: boolean }) {
  return <div className="control-row" data-testid={`control-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`}><span>{label}</span><Amount value={value} signed={signed} strong={strong} /></div>;
}
