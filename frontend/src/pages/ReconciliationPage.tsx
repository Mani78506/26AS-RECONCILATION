import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowUp, ArrowUpDown, CloudUpload, Download, RefreshCw, RotateCcw, Search } from "lucide-react";
import { toast } from "sonner";
import { Amount, ClaimBadge, Drawer, EmptyState, ErrorNotice, IdentityBadge, MethodBadge, Pagination, PageHeader, ResultBadge, TableSkeleton } from "../components/common";
import { ResultDetail } from "../components/ResultDetail";
import { useWorkspace } from "../context/WorkspaceContext";
import { fmtDate, IDENTITY_LABELS, inr, maskPan, METHOD_LABELS, num, RESULT_LABELS } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import type { DeductorSummary, ReconciliationResult } from "../types";
import { getReconciliationStatusPresentation } from "../lib/reconciliationStatus";

type Col = { key: string; label: string; sortable?: boolean; num?: boolean; render: (r: ReconciliationResult) => React.ReactNode; width?: number };
const FILTER_KEYS = ["financial_year", "quarter", "customer_code", "deductor", "tan", "section", "result", "claimability", "identity_status", "match_method"] as const;
const QUICK_FILTERS = [
  ["All", ""], ["Matched", "MATCHED_CLAIMABLE"], ["Mismatch", "AMOUNT_MISMATCH"],
  ["Missing in 26AS", "MISSING_IN_26AS"], ["Missing in Books", "MISSING_IN_BOOKS"],
  ["Deductor Unmapped", "IDENTITY_UNMAPPED"], ["Not Claimable", "MATCHED_NOT_CLAIMABLE"],
  ["Duplicates", "DUPLICATE_BOOK,DUPLICATE_26AS"],
] as const;
const ANALYSIS_QUICK_FILTERS = [["All", ""], ["Consistent", "CONSISTENT_WITH_CONFIGURED_RULE"], ["TDS Difference", "TDS_DIFFERENCE"], ["Not Determinable", "NOT_DETERMINABLE"]] as const;
const ANALYSIS_COLS: Col[] = [
  { key: "transaction_id", label: "26AS Entry", sortable: true, render: (r) => <b className="mono">{r.transaction_id}</b> },
  { key: "deductor_name", label: "Deductor / Payer", sortable: true, render: (r) => <span className="ellipsis" title={r.deductor_name}>{r.deductor_name || "—"}</span>, width: 220 },
  { key: "tan", label: "TAN", sortable: true, render: (r) => <span className="mono">{r.tan || "—"}</span> },
  { key: "statement_quarter", label: "Quarter", render: (r) => r.statement_quarter || "—" },
  { key: "section", label: "Section", sortable: true, render: (r) => <span className="mono">{r.section || "—"}</span> },
  { key: "amount_paid", label: "Amount Credited / Paid", num: true, render: (r) => <Amount value={r.amount_paid ?? r.statement_entries?.[0]?.amount_paid} /> },
  { key: "tax_deducted", label: "TDS Deducted", sortable: true, num: true, render: (r) => <Amount value={r.tax_deducted} /> },
  { key: "tds_expected", label: "TDS Expected", sortable: true, num: true, render: (r) => r.tds_expected == null ? <span className="muted-copy">Not Determinable</span> : <Amount value={r.tds_expected} /> },
  { key: "difference", label: "Difference", sortable: true, num: true, render: (r) => <Amount value={r.difference} signed /> },
  { key: "result", label: "Analysis", sortable: true, render: (r) => <ResultBadge result={r.analysis_status || r.result} /> },
  { key: "reason", label: "Calculation reason", render: (r) => <span className="ellipsis wide" title={r.reason}>{r.reason || "—"}</span>, width: 320 },
];

export default function ReconciliationPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [sp, setSp] = useSearchParams();
  const { runId, run, runLoading, runError, refreshRun, processing } = useWorkspace();
  // Historical runs without workflow metadata remain full reconciliations.
  const analysisOnly = run?.workflow === "26AS_ONLY";
  const [searchText, setSearchText] = useState(sp.get("search") || "");
  const [advancedFilters, setAdvancedFilters] = useState(false);
  const update = (patch: Record<string, string>) => { const next = new URLSearchParams(sp); Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k))); setSp(next, { replace: true }); };
  useEffect(() => { const t = setTimeout(() => { if ((sp.get("search") || "") !== searchText) update({ search: searchText, page: "1" }); }, 350); return () => clearTimeout(t); }, [searchText]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setSearchText(""); setAdvancedFilters(false); setSp(new URLSearchParams(), { replace: true }); }, [runId]); // A different run must never retain the prior run's filters/detail state.

  const activeFilterKeys = analysisOnly ? FILTER_KEYS.filter((key) => !["customer_code", "claimability", "identity_status", "match_method"].includes(key)) : FILTER_KEYS;
  const query = useMemo<Record<string, any>>(() => ({ run_id: runId, page: Number(sp.get("page") || 1), page_size: Number(sp.get("page_size") || 50), search: sp.get("search") || "", sort: sp.get("sort") || "seq", order: sp.get("order") || "asc", ...Object.fromEntries(activeFilterKeys.map((k) => [k, sp.get(k) || ""])) }), [sp, runId, analysisOnly]);
  const q = useQuery({ queryKey: ["results", query], queryFn: () => api.results(query), enabled: !!runId && !processing });
  const overview = useQuery({ queryKey: ["reconciliation-overview", runId], queryFn: () => api.summary(runId), enabled: !!runId && analysisOnly && !processing });
  const sortBy = (key: string) => update({ sort: key, order: query.sort === key && query.order === "asc" ? "desc" : "asc", page: "1" });
  const activeFilters = activeFilterKeys.filter((k) => sp.get(k)).length + (sp.get("search") ? 1 : 0);
  const facets = q.data?.facets || {};
  const cols = ANALYSIS_COLS;
  const [exporting, setExporting] = useState(false);
  const exportNow = async () => { setExporting(true); try { const name = await api.downloadReport("ca_reconciliation", "xlsx", runId); toast.success("Export ready", { description: name }); } catch (e) { toast.error(getErrorMessage(e, "Export failed.")); } finally { setExporting(false); } };

  const noRun = !runLoading && !runError && !runId;
  return <>
    <PageHeader eyebrow={analysisOnly ? "26AS ANALYSIS" : "REVIEW"} title={analysisOnly ? "26AS-only TDS analysis" : "Reconciliation"} subtitle={run ? `${run.assessee_name || "Unnamed assessee"} · FY ${run.financial_year || "—"} · ${q.data?.total ?? run.summary?.result_count ?? 0} ${analysisOnly ? "26AS entries" : "result rows"}` : "Review transaction outcomes, claimability and recommended actions."} action={<button className="secondary-button" data-testid="export-results-button" disabled={!runId || exporting} onClick={exportNow}><Download size={15} /> {exporting ? "Exporting…" : "Export Excel"}</button>} />
    {run && <section className={`reconciliation-context ${analysisOnly ? "analysis-context" : ""}`} data-testid="reconciliation-context"><div><span>ASSESSEE</span><b>{run.assessee_name || "Assessee not recorded"}</b></div><div><span>PAN</span><b className="mono">{maskPan(run.assessee_pan)}</b></div><div><span>FINANCIAL YEAR</span><b>{run.financial_year || "—"}</b></div>{analysisOnly && <><div><span>SOURCE</span><b>26AS / Form 16A</b></div><div><span>MODE</span><b>26AS-only TDS analysis</b></div></>}<div><span>{analysisOnly ? "ANALYSIS RUN" : "RECONCILIATION RUN"}</span><b>{run.status === "COMPLETED" ? "Completed" : run.status}</b><small>{run.finished_at ? fmtDate(run.finished_at) : "In progress"}</small></div></section>}
    {run?.summary && <section className="reconciliation-summary" data-testid="reconciliation-summary">{analysisOnly ? <><Summary label="Deductors" value={num(overview.data?.summary?.deductor_count ?? run.summary.deductor_count ?? 0)} /><Summary label="26AS entries" value={num(run.summary.statement_count)} /><Summary label="Amount credited / paid" value={inr(overview.data?.summary?.amount_paid_total ?? run.summary.amount_paid_total)} /><Summary label="TDS expected" value={inr(run.summary.expected_tds_calculated)} /><Summary label="TDS deducted" value={inr(run.summary.statement_tax_deducted)} /><Summary label="Difference" value={inr(run.summary.difference)} tone="difference" /><Summary label="Consistent" value={num(run.summary.consistent_count || 0)} /><Summary label="TDS differences" value={num(run.summary.difference_count || 0)} /><Summary label="Not determinable" value={num(run.summary.not_determinable_count || 0)} /></> : <><div className="reconciliation-primary-totals"><Summary label="Books TDS Expected" value={inr(run.summary.books_tds_expected)} /><Summary label="26AS TDS Deducted" value={inr(run.summary.statement_tax_deducted)} /><Summary label="Difference (Expected − Deducted)" value={inr(run.summary.difference)} tone="difference" /><Summary label="Matched" value={num((run.summary.matched_claimable_count || 0) + (run.summary.matched_not_claimable_count || 0))} /></div><div className="reconciliation-attention-strip" aria-label="Reconciliation attention counts"><span>Needs attention</span><b>Amount mismatch <em>{num(run.summary.result_counts?.AMOUNT_MISMATCH || 0)}</em></b><b>Missing in 26AS <em>{num(run.summary.result_counts?.MISSING_IN_26AS || 0)}</em></b><b>Missing in Books <em>{num(run.summary.result_counts?.MISSING_IN_BOOKS || 0)}</em></b><b>Deductor unmapped <em>{num(run.summary.result_counts?.IDENTITY_UNMAPPED || 0)}</em></b><b>Not claimable <em>{num(run.summary.matched_not_claimable_count)}</em></b></div></>}</section>}
    {analysisOnly && <DeductorOverview items={overview.data?.deductor_summary || []} runId={runId} />}
    {runError && <ErrorNotice message="Reconciliation service is unavailable. Check the backend connection and try again." onRetry={refreshRun} />}
    {q.isError && <ErrorNotice message={getErrorMessage(q.error)} onRetry={() => q.refetch()} />}

    <div className="result-quick-filters" role="group" aria-label={analysisOnly ? "26AS analysis filters" : "Reconciliation status filters"}>{(analysisOnly ? ANALYSIS_QUICK_FILTERS : QUICK_FILTERS).map(([label, value]) => <button key={label} className={query.result === value || (!value && !query.result) ? "active" : ""} onClick={() => update({ result: value, page: "1" })}>{label}</button>)}</div>
    <div className="filter-bar reconciliation-filter-bar" data-testid="filter-bar">
      <div className="search-field"><Search size={15} /><input data-testid="transaction-search-input" aria-label="Search transactions" placeholder={analysisOnly ? "Search 26AS entry, deductor, TAN, section or reason…" : "Search transaction, Books Customer, Deductor, TAN or reason…"} value={searchText} onChange={(e) => setSearchText(e.target.value)} /></div>
      <FilterSelect id="financial_year" label="FY" value={query.financial_year} options={(facets.financial_year || (run?.financial_year ? [run.financial_year] : [])).map((v: string) => [v, v])} onChange={update} />
      <FilterSelect id="quarter" label="Quarter" value={query.quarter} options={["Q1", "Q2", "Q3", "Q4"].map((v) => [v, v])} onChange={update} />
      <FilterSelect id="result" label="Result" value={query.result} options={Object.entries(RESULT_LABELS)} onChange={update} />
      <button className={advancedFilters ? "secondary-button active" : "secondary-button"} aria-expanded={advancedFilters} onClick={() => setAdvancedFilters((open) => !open)}>More filters{advancedFilters ? " · Hide" : ""}</button>
      <button className="secondary-button" data-testid="reset-filters-button" disabled={!activeFilters} onClick={() => { setSearchText(""); setSp(new URLSearchParams(), { replace: true }); }}><RotateCcw size={14} /> Reset{activeFilters ? ` (${activeFilters})` : ""}</button>
      {advancedFilters && <div className="filter-bar-expanded">{!analysisOnly && <FilterSelect id="customer_code" label="Books Customer" value={query.customer_code} options={(facets.customers || []).map((c: any) => [c.code, `${c.code} · ${c.name}`])} onChange={update} />}<FilterSelect id="deductor" label="Deductor" value={query.deductor} options={(facets.deductors || []).map((v: string) => [v, v])} onChange={update} /><FilterSelect id="tan" label="TAN" value={query.tan} options={(facets.tan || []).map((v: string) => [v, v])} onChange={update} /><FilterSelect id="section" label="Section" value={query.section} options={(facets.section || []).map((v: string) => [v, v])} onChange={update} />{!analysisOnly && <><FilterSelect id="claimability" label="Claimability" value={query.claimability} options={[["CLAIMABLE", "Claimable"], ["NOT_CLAIMABLE", "Not Claimable"], ["NOT_APPLICABLE", "N/A"]]} onChange={update} /><FilterSelect id="identity_status" label="Identity" value={query.identity_status} options={Object.entries(IDENTITY_LABELS)} onChange={update} /><FilterSelect id="match_method" label="Method" value={query.match_method} options={Object.entries(METHOD_LABELS)} onChange={update} /></>}</div>}
    </div>

    <section className="workspace-section table-section" data-testid="results-table-section">
      <div className="table-toolbar"><div className="table-meta"><b data-testid="results-count">{q.data ? `${q.data.total} ${analysisOnly ? "26AS entr" : "record"}${q.data.total === 1 ? "y" : analysisOnly ? "ies" : "s"}` : "—"}</b><span>{processing ? (analysisOnly ? "Re-running 26AS analysis…" : "Re-running reconciliation…") : analysisOnly ? "Configured-rule analysis of 26AS entries; no Books data is used" : "Live results from the reconciliation engine"}</span></div><button className="icon-button" data-testid="refresh-table-button" aria-label="Refresh results" onClick={() => q.refetch()}><RefreshCw size={15} className={q.isFetching ? "spin" : ""} /></button></div>
      {(runLoading || (q.isLoading && runId) || processing) ? <TableSkeleton cols={Math.min(cols.length, 10)} />
        : noRun ? <EmptyState title="No reconciliation has been processed yet" text="Upload Books and 26AS to populate the reconciliation table with engine results." action={<button className="primary-button" data-testid="begin-workflow-button" onClick={() => navigate("/reconciliation/new")}><CloudUpload size={15} /> Begin workflow</button>} />
        : q.data && q.data.total === 0 ? <EmptyState title={activeFilters ? "No rows match the current filters" : analysisOnly ? "No 26AS entries to analyse" : "This run produced no result rows"} text={activeFilters ? "Adjust or reset the filters to see more results." : analysisOnly ? "Expected TDS may be not determinable when no applicable authorised rule or calculation inputs are available." : "Both source files parsed but contained no reconcilable rows."} action={activeFilters ? <button className="secondary-button" data-testid="empty-reset-filters-button" onClick={() => { setSearchText(""); setSp(new URLSearchParams(), { replace: true }); }}>Reset filters</button> : undefined} />
        : q.data && <>
          {analysisOnly ? <div className="table-scroll" data-testid="results-table-scroll"><table className="data-table" data-testid="results-table"><thead><tr>{cols.map((c) => <th key={c.key} className={c.num ? "num" : ""} style={c.width ? { minWidth: c.width } : undefined} aria-sort={query.sort === c.key ? (query.order === "asc" ? "ascending" : "descending") : undefined}>{c.sortable ? <button className="sort-button" data-testid={`sort-${c.key}`} onClick={() => sortBy(c.key)}>{c.label}{query.sort === c.key ? (query.order === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ArrowUpDown size={12} className="dim" />}</button> : c.label}</th>)}</tr></thead><tbody>{q.data.items.map((r) => <tr key={r.id} tabIndex={0} data-testid={`result-row-${r.transaction_id}`} onClick={() => navigate(`/reconciliation/${encodeURIComponent(r.id)}?${sp.toString()}`)} onKeyDown={(e) => e.key === "Enter" && navigate(`/reconciliation/${encodeURIComponent(r.id)}?${sp.toString()}`)}>{cols.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.render(r)}</td>)}</tr>)}</tbody></table></div> : <div className="full-result-grid" data-testid="full-reconciliation-card-grid">{q.data.items.map((row) => <FullResultCard key={row.id} row={row} onOpen={() => navigate(`/reconciliation/${encodeURIComponent(row.id)}?${sp.toString()}`)} />)}</div>}
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPage={(p) => update({ page: String(p) })} onPageSize={(s) => update({ page_size: String(s), page: "1" })} />
        </>}
    </section>

    <Drawer open={!!id} onClose={() => navigate(`/reconciliation?${sp.toString()}`)} title={<span className="drawer-title"><button className="back-link inline" data-testid="back-to-reconciliation-button" onClick={() => navigate(`/reconciliation?${sp.toString()}`)}><ArrowLeft size={14} /></button> {analysisOnly ? "26AS entry analysis" : "Reconciliation review detail"}</span>} subtitle={analysisOnly ? "26AS deduction, configured-rule calculation and analysis rationale." : "Review the complete system finding, source evidence, recommended CA action and working-paper commentary."} testId="result-drawer">{id && <ResultDetail id={decodeURIComponent(id)} />}</Drawer>
  </>;
}

function FilterSelect({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: [string, string][]; onChange: (patch: Record<string, string>) => void }) {
  return <select aria-label={label} data-testid={`filter-${id}`} value={value} className={value ? "active" : ""} onChange={(e) => onChange({ [id]: e.target.value, page: "1" })}><option value="">{label}: All</option>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>;
}

function FullResultCard({ row, onOpen }: { row: ReconciliationResult; onOpen: () => void }) {
  const status = getReconciliationStatusPresentation(row);
  const bookQuarter = row.books_quarter || "—";
  const statementQuarter = row.statement_quarter || "—";
  const isGroup = row.group_size > 2;
  return <article className={`full-result-card sev-${row.severity.toLowerCase()}`} data-testid={`result-row-${row.transaction_id}`} tabIndex={0} onClick={onOpen} onKeyDown={(event) => event.key === "Enter" && onOpen()}>
    <header>
      <div>
        <ResultBadge result={row.result} compact />
        <h3 className="mono">{row.transaction_id || "Source record"}</h3>
        <p>{status.label}{isGroup ? ` · ${row.group_size} source records in this group` : ""}</p>
      </div>
      <button type="button" className="text-button full-card-open" onClick={(event) => { event.stopPropagation(); onOpen(); }}>Open full review</button>
    </header>
    <div className="full-party-grid">
      <section><span>BOOKS CUSTOMER</span><b>{row.customer || "—"}</b><small>{row.customer_code ? `Code: ${row.customer_code}` : "No Books customer linked"}</small></section>
      <section><span>26AS DEDUCTOR</span><b>{row.deductor_name || "—"}</b><small>TAN: <em className="mono">{row.tan || "—"}</em></small></section>
    </div>
    <div className="full-card-tags"><span>FY <b>{row.financial_year || "—"}</b></span><span>Quarter <b>{bookQuarter === statementQuarter ? bookQuarter : `Books ${bookQuarter} · 26AS ${statementQuarter}`}</b></span><span>Section <b className="mono">{row.section || "—"}</b></span><IdentityBadge value={row.identity_status} /><MethodBadge value={row.match_method} /><ClaimBadge value={row.claimability} /></div>
    <section className="full-tds-check">
      <div className="full-check-heading"><b>TDS reconciliation</b><span>Books TDS Expected vs 26AS TDS Deducted</span></div>
      <div className="full-check-values"><div><span>Books TDS Expected</span><b><Amount value={row.tds_expected} /></b></div><div><span>26AS TDS Deducted</span><b><Amount value={row.tax_deducted} /></b></div><div><span>Difference</span><b><Amount value={row.difference} signed /></b></div></div>
    </section>
    <footer><section className="card-review-copy"><span>SYSTEM FINDING</span><p>{row.reason || status.explanation}</p></section><section className="card-review-copy action"><span>RECOMMENDED CA ACTION</span><p>{row.recommended_action || "Open the full review to examine the source evidence."}</p></section><div className={`commentary-indicator ${row.commentary_summary?.exists ? "added" : ""}`} data-testid={`commentary-indicator-${row.transaction_id}`}>{row.commentary_summary?.exists ? <><b>CA commentary added</b><span>{row.commentary_summary.review_status === "REVIEWED" ? "Reviewed" : "New"} · Open full review to view</span></> : <><b>CA commentary not added</b><span>Open full review to record the CA conclusion</span></>}</div></footer>
  </article>;
}

function Summary({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className={`reconciliation-summary-card ${tone || ""}`}><span>{label}</span><b>{value}</b></div>;
}

function DeductorOverview({ items, runId }: { items: DeductorSummary[]; runId: string | null }) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  if (!items.length) return null;
  return <section className="workspace-section table-section" data-testid="deductor-summary"><div className="table-toolbar"><div className="table-meta"><b>Deductors / Payers</b><span>Select a deductor to expand its 26AS entries here.</span></div></div><div className="table-scroll"><table className="data-table"><thead><tr><th>Deductor / Payer</th><th>TAN</th><th>Sections</th><th className="num">Entries</th><th className="num">Amount credited / paid</th><th className="num">TDS expected</th><th className="num">TDS deducted</th><th className="num">Difference</th><th>Analysis</th></tr></thead><tbody>{items.map((item) => { const key = `${item.tan}:${item.deductor_name}`; const open = selectedKey === key; return <Fragment key={key}><tr tabIndex={0} aria-expanded={open} className={open ? "current" : ""} onClick={() => setSelectedKey(open ? null : key)} onKeyDown={(event) => event.key === "Enter" && setSelectedKey(open ? null : key)}><td><b>{item.deductor_name}</b><small className="inline-drilldown-hint">{open ? "Hide entries" : "View entries"}</small></td><td className="mono">{item.tan || "—"}</td><td>{item.sections.join(", ") || "—"}</td><td className="num">{num(item.entry_count)}</td><td className="num">{inr(item.amount_paid)}</td><td className="num">{item.not_determinable_count ? <span title={`${item.not_determinable_count} entry/entries not determinable`}>{inr(item.tds_expected)}*</span> : inr(item.tds_expected)}</td><td className="num">{inr(item.tds_deducted)}</td><td className="num"><Amount value={item.difference} signed /></td><td><ResultBadge result={item.analysis} /></td></tr>{open && <tr className="deductor-drilldown-row"><td colSpan={9} className="deductor-drilldown-cell"><DeductorTransactions runId={runId} deductor={item.deductor_name} tan={item.tan} /></td></tr>}</Fragment>; })}</tbody></table></div><small className="muted-copy">* Expected TDS total excludes entries that are not determinable; it is never assumed as zero.</small></section>;
}

function DeductorTransactions({ runId, deductor, tan }: { runId: string | null; deductor: string; tan: string }) {
  const entries = useQuery({ queryKey: ["deductor-transactions", runId, tan, deductor], queryFn: () => api.results({ run_id: runId, deductor, tan, page_size: 200, sort: "statement_date", order: "asc" }), enabled: !!runId });
  if (entries.isLoading) return <div className="deductor-drilldown-status">Loading 26AS entries…</div>;
  if (entries.isError) return <div className="deductor-drilldown-status">Unable to load this deductor’s 26AS entries. <button className="text-button" onClick={() => entries.refetch()}>Retry</button></div>;
  const rows = entries.data?.items || [];
  return <div className="deductor-drilldown"><div className="deductor-drilldown-title"><b>{deductor}</b><span className="mono">TAN: {tan}</span><small>{rows.length} 26AS entr{rows.length === 1 ? "y" : "ies"}</small></div><div className="deductor-drilldown-scroll"><table><thead><tr><th>26AS entry</th><th>Date</th><th>Quarter</th><th>Section</th><th className="num">Amount credited / paid</th><th className="num">TDS expected</th><th className="num">TDS deducted</th><th className="num">Difference</th><th>Analysis</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td className="mono">{row.transaction_id}</td><td>{fmtDate(row.statement_date)}</td><td>{row.statement_quarter || "—"}</td><td className="mono">{row.section || "—"}</td><td className="num"><Amount value={row.amount_paid ?? row.statement_entries?.[0]?.amount_paid} /></td><td className="num">{row.tds_expected == null ? "Not Determinable" : <Amount value={row.tds_expected} />}</td><td className="num"><Amount value={row.tax_deducted} /></td><td className="num"><Amount value={row.difference} signed /></td><td><ResultBadge result={row.analysis_status || row.result} /></td></tr>)}</tbody></table></div></div>;
}
