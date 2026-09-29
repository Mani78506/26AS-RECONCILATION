import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CloudUpload, Eye, MessageSquarePlus, RefreshCw, Search, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { Amount, Drawer, EmptyState, ErrorNotice, PageHeader, Pagination, ResultBadge, SeverityBadge, StatusBadge, TableSkeleton } from "../components/common";
import { ResultDetail } from "../components/ResultDetail";
import { useWorkspace } from "../context/WorkspaceContext";
import { CATEGORY_LABELS, fmtDateTime, STATUS_LABELS } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import type { ExceptionStatus, ReconciliationResult, SalesTdsException } from "../types";

const CATEGORIES = ["ALL", "AMOUNT_MISMATCH", "MISSING_IN_BOOKS", "MISSING_IN_26AS", "IDENTITY", "DUPLICATES", "NOT_CLAIMABLE"];
const STATUSES: ExceptionStatus[] = ["OPEN", "IN_REVIEW", "RESOLVED", "IGNORED"];

const is26ASOnly = (r: ReconciliationResult) => r.result === "MISSING_IN_BOOKS" || r.result === "IDENTITY_UNMAPPED";
const booksCustomer = (r: ReconciliationResult) => is26ASOnly(r) ? "—" : r.customer || "—";
const deductor = (r: ReconciliationResult) => r.result === "MISSING_IN_26AS" ? "—" : r.deductor_name || "—";
const tans = (r: ReconciliationResult) => r.result === "MISSING_IN_26AS" ? "—" : (r.matched_tans?.length ? r.matched_tans.join(", ") : r.tan || "—");
const exceptionAmount = (r: ReconciliationResult) => is26ASOnly(r) ? r.tax_deducted : r.tds_expected ?? r.tax_deducted;

const salesStatus = (value?: string | null) => value ? value.replaceAll("_", " ") : "â€”";

function SalesExceptionWorkbench({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [focus, setFocus] = useState<"ALL" | "IDENTITY" | "AMOUNT" | "TDS">("ALL");
  const q = useQuery({ queryKey: ["sales-tds-exceptions", runId], queryFn: () => api.salesTdsExceptions(runId), enabled: !!runId });
  const items = q.data?.items || [];
  const counts = {
    IDENTITY: items.filter((item) => item.identity_status !== "CONFIRMED").length,
    AMOUNT: items.filter((item) => item.amount_status !== "AMOUNT_MATCHED").length,
    TDS: items.filter((item) => item.tds_status !== "TDS_MATCHED").length,
  };
  const filtered = useMemo(() => items.filter((item) => {
    const matchesFocus = focus === "ALL" || (focus === "IDENTITY" && item.identity_status !== "CONFIRMED") || (focus === "AMOUNT" && item.amount_status !== "AMOUNT_MATCHED") || (focus === "TDS" && item.tds_status !== "TDS_MATCHED");
    const haystack = [item.transaction_id, item.sales_customer, item.deductor, item.tan, item.identity_reason, item.reason, item.recommended_action].join(" ").toLowerCase();
    return matchesFocus && (!search.trim() || haystack.includes(search.trim().toLowerCase()));
  }), [items, focus, search]);
  return <>
    <PageHeader eyebrow="SALES + TDS + 26AS" title="Review queue" subtitle="Each card explains what needs attention, why it needs attention, and where to inspect the underlying evidence." />
    {q.isError && <ErrorNotice message={getErrorMessage(q.error)} onRetry={() => q.refetch()} />}
    <section className="sales-exception-intro"><div><span className="eyebrow">HOW TO USE THIS QUEUE</span><h2>Resolve identity before interpreting amounts</h2><p>Start with the identity finding. Sales and TDS comparisons remain separate so a missing amount basis is never treated as an amount difference.</p></div><button className="icon-button" aria-label="Refresh sales exceptions" onClick={() => q.refetch()}><RefreshCw size={15} className={q.isFetching ? "spin" : ""} /></button></section>
    <section className="sales-exception-overview" aria-label="Exception categories"><button className={focus === "ALL" ? "active" : ""} onClick={() => setFocus("ALL")}><span>Needs attention</span><b>{items.length}</b><small>All relationships requiring review</small></button><button className={focus === "IDENTITY" ? "active" : ""} onClick={() => setFocus("IDENTITY")}><span>Identity first</span><b>{counts.IDENTITY}</b><small>Confirm customer ↔ deductor evidence</small></button><button className={focus === "AMOUNT" ? "active" : ""} onClick={() => setFocus("AMOUNT")}><span>Sales amount</span><b>{counts.AMOUNT}</b><small>Check whether a comparison basis exists</small></button><button className={focus === "TDS" ? "active" : ""} onClick={() => setFocus("TDS")}><span>TDS evidence</span><b>{counts.TDS}</b><small>Review ledger ↔ 26AS tax evidence</small></button></section>
    <section className="workspace-section" data-testid="sales-exception-workbench"><div className="sales-exception-toolbar"><label className="search-field"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search deductor, customer, TAN, reason or action" aria-label="Search Sales workflow exceptions" /></label><span>{filtered.length} of {items.length} shown</span></div>
      {q.isLoading ? <TableSkeleton rows={5} cols={4} /> : !items.length ? <EmptyState title="No Sales workflow exceptions require attention" text="All persisted Sales + TDS + 26AS relationships are currently fully reconciled." /> : !filtered.length ? <EmptyState title="No exceptions match this view" text="Try another category or clear the search." action={<button className="secondary-button" onClick={() => { setFocus("ALL"); setSearch(""); }}>Clear filters</button>} /> : <div className="sales-exception-list">{filtered.map((row: SalesTdsException) => <article key={row.id} className="sales-exception-card" data-testid={`sales-exception-${row.id}`}><header><div><ResultBadge result={row.overall_status} compact /><b className="mono">{row.transaction_id}</b><small>{row.assessee_name || "Unknown assessee"} · {row.financial_year || "FY not reported"}</small></div><button className="mini-button" onClick={() => navigate(`/reconciliation?detail=${encodeURIComponent(row.id)}`)}><Eye size={13} /> Open evidence</button></header><div className="sales-exception-identity"><div><span>Sales customer</span><b>{row.sales_customer || "—"}</b></div><div><span>26AS deductor</span><b>{row.deductor || "—"}</b></div><div><span>TAN</span><b className="mono">{row.tan || "—"}</b></div></div><div className="sales-exception-checks"><section className={row.identity_status === "CONFIRMED" ? "clear" : "attention"}><span className="eyebrow">1 · IDENTITY</span><ResultBadge result={row.identity_status} compact /><p><b>{salesStatus(row.identity_method)}</b><br />{row.identity_reason || "No additional identity explanation was recorded."}</p></section><section className={row.amount_status === "AMOUNT_MATCHED" ? "clear" : "attention"}><span className="eyebrow">2 · SALES AMOUNT</span><ResultBadge result={row.amount_status} compact /><p>Sales <Amount value={row.sales_amount} /> · 26AS paid <Amount value={row.statement_amount_paid} /> · Difference <Amount value={row.amount_difference} signed /></p></section><section className={row.tds_status === "TDS_MATCHED" ? "clear" : "attention"}><span className="eyebrow">3 · TDS</span><ResultBadge result={row.tds_status} compact /><p>Books <Amount value={row.tds_expected} /> · 26AS deducted <Amount value={row.statement_tds_deducted} /> · Difference <Amount value={row.tds_difference} signed /></p></section></div><footer><span className="eyebrow">NEXT ACTION</span><b>{row.recommended_action || row.reason || "Open evidence and review the source records."}</b></footer></article>)}</div>}
    </section>
  </>;
}
export default function ExceptionsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const { runId, run, runLoading, runError, refreshRun, processing, user } = useWorkspace();
  const [search, setSearch] = useState(sp.get("search") || "");
  const [focus, setFocus] = useState<string | null>(sp.get("focus"));
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState("");
  const update = (patch: Record<string, string>) => { const next = new URLSearchParams(sp); Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k))); setSp(next, { replace: true }); };
  useEffect(() => { const t = setTimeout(() => { if ((sp.get("search") || "") !== search) update({ search, page: "1" }); }, 350); return () => clearTimeout(t); }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const query = useMemo(() => ({ run_id: runId, category: sp.get("category") || "ALL", status: sp.get("status") || "ALL", search: sp.get("search") || "", page: Number(sp.get("page") || 1), page_size: Number(sp.get("page_size") || 50), sort: sp.get("sort") || "severity" }), [sp, runId]);
  const q = useQuery({ queryKey: ["exceptions", query], queryFn: () => api.exceptions(query), enabled: !!runId && !processing });
  const focused = q.data?.items.find((r) => r.id === focus) || null;
  const mut = useMutation({ mutationFn: ({ id, body }: { id: string; body: { status?: string; note?: string; assignee?: string } }) => api.updateException(id, { ...body, reviewer: user.name }), onSuccess: (_, v) => { qc.invalidateQueries({ queryKey: ["exceptions"] }); qc.invalidateQueries({ queryKey: ["result", v.id] }); toast.success(v.body.status ? `Marked ${STATUS_LABELS[v.body.status]}` : v.body.note ? "Note added" : "Assignee updated"); setNote(""); }, onError: (e) => toast.error(getErrorMessage(e, "Could not update the exception.")) });
  const setStatus = (r: ReconciliationResult, status: ExceptionStatus) => mut.mutate({ id: r.id, body: { status } });
  const noRun = !runLoading && !runError && !runId;

  if (run?.workflow === "SALES_TDS_26AS" && runId) return <SalesExceptionWorkbench runId={runId} />;

  return <>
    <PageHeader eyebrow="OPERATIONS QUEUE" title="Exception Workbench" subtitle={run ? `${q.data?.counts?.ALL ?? run.summary?.exceptions_count ?? 0} items require CA attention · ${q.data?.status_counts?.OPEN ?? "—"} open` : "Items requiring CA attention, with reasons, recommended actions and review status."} />
    {runError && <ErrorNotice message="Reconciliation service is unavailable. Check the backend connection and try again." onRetry={refreshRun} />}
    {q.isError && <ErrorNotice message={getErrorMessage(q.error)} onRetry={() => q.refetch()} />}
    <div className="tab-bar" role="tablist" data-testid="exception-categories">{CATEGORIES.map((c) => <button key={c} role="tab" aria-selected={query.category === c} className={`tab ${query.category === c ? "active" : ""}`} data-testid={`category-tab-${c}`} onClick={() => update({ category: c === "ALL" ? "" : c, page: "1" })}>{CATEGORY_LABELS[c]}<em>{q.data?.counts?.[c] ?? 0}</em></button>)}</div>
    <div className="filter-bar" data-testid="exception-filters"><div className="search-field"><Search size={15} /><input data-testid="exception-search-input" aria-label="Search exceptions" placeholder="Search transaction, Books Customer, Deductor, TAN or reason…" value={search} onChange={(e) => setSearch(e.target.value)} /></div><select aria-label="Status" data-testid="exception-status-filter" value={query.status} onChange={(e) => update({ status: e.target.value === "ALL" ? "" : e.target.value, page: "1" })}><option value="ALL">Status: All</option>{STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]} ({q.data?.status_counts?.[s] ?? 0})</option>)}</select><select aria-label="Sort" data-testid="exception-sort" value={query.sort} onChange={(e) => update({ sort: e.target.value })}><option value="severity">Sort: Severity</option><option value="difference">Sort: Difference</option><option value="customer">Sort: Books Customer</option><option value="transaction_id">Sort: Record / Transaction</option></select></div>

    <section className="workspace-section table-section" data-testid="exceptions-table-section">
      <div className="table-toolbar"><div className="table-meta"><b data-testid="exceptions-count">{q.data ? `${q.data.total} exception${q.data.total === 1 ? "" : "s"}` : "—"}</b><span>Exceptions are never deleted — resolve or ignore them with a note for the audit trail.</span></div><button className="icon-button" data-testid="refresh-exceptions-button" aria-label="Refresh" onClick={() => q.refetch()}><RefreshCw size={15} className={q.isFetching ? "spin" : ""} /></button></div>
      {(runLoading || (q.isLoading && runId) || processing) ? <TableSkeleton cols={9} /> : noRun ? <EmptyState title="No reconciliation has been processed yet" text="Exceptions appear after processing Books and 26AS." action={<button className="primary-button" data-testid="begin-workflow-button" onClick={() => navigate("/reconciliation/new")}><CloudUpload size={15} /> Begin workflow</button>} />
        : q.data && q.data.total === 0 ? <EmptyState icon={Check} title={query.category !== "ALL" || query.status !== "ALL" || query.search ? "No exceptions match these filters" : "No exceptions require attention"} text={query.category !== "ALL" || query.status !== "ALL" || query.search ? "Try another category or status." : "Every books transaction is matched and claimable."} />
        : q.data && <><div className="table-scroll"><table className="data-table" data-testid="exceptions-table"><thead><tr><th>Severity</th><th>Record / Transaction</th><th>Books Customer</th><th>Deductor</th><th>TAN</th><th>Quarter</th><th className="num">Amount</th><th className="num">Difference</th><th>Issue</th><th style={{ minWidth: 300 }}>Reason</th><th style={{ minWidth: 260 }}>Recommended Action</th><th>Status</th><th>Assignee</th><th>Actions</th></tr></thead>
          <tbody>{q.data.items.map((r) => <tr key={r.id} data-testid={`exception-row-${r.transaction_id}`} className={focus === r.id ? "current" : ""}><td><SeverityBadge value={r.severity} /></td><td><b className="mono">{r.transaction_id}</b></td><td><span className="ellipsis" title={booksCustomer(r)}>{booksCustomer(r)}</span></td><td><span className="ellipsis" title={deductor(r)}>{deductor(r)}</span></td><td className="mono" title={tans(r)}>{tans(r)}</td><td>{r.books_quarter || r.statement_quarter || "—"}</td><td className="num"><Amount value={exceptionAmount(r)} /></td><td className="num"><Amount value={r.difference} signed /></td><td><ResultBadge result={r.result} compact /></td><td><span className="ellipsis wide" title={r.reason}>{r.reason}</span></td><td><span className="ellipsis wide" title={r.recommended_action}>{r.recommended_action}</span></td><td><StatusBadge value={r.exception_status || "OPEN"} />{!!r.notes?.length && <small className="note-count">{r.notes.length} note{r.notes.length === 1 ? "" : "s"}</small>}</td><td>{r.assignee || "—"}</td>
            <td className="actions"><button className="mini-button" data-testid={`review-exception-${r.transaction_id}`} title="Open detail" onClick={() => { setFocus(r.id); update({ focus: r.id }); }}><Eye size={13} /> Review</button>{r.exception_status !== "RESOLVED" && <button className="mini-button green" data-testid={`resolve-exception-${r.transaction_id}`} disabled={mut.isPending} onClick={() => setStatus(r, "RESOLVED")}><Check size={13} /> Resolve</button>}</td></tr>)}</tbody></table></div>
          <Pagination page={q.data.page} pageSize={q.data.page_size} total={q.data.total} onPage={(p) => update({ page: String(p) })} onPageSize={(s) => update({ page_size: String(s), page: "1" })} /></>}
    </section>

    <Drawer open={!!focus} onClose={() => { setFocus(null); update({ focus: "" }); }} title="Exception review" subtitle={focused ? `${focused.transaction_id} · Books Customer: ${booksCustomer(focused)} · Deductor: ${deductor(focused)}` : undefined} testId="exception-drawer">
      {focus && <>
        <div className="exception-actions" data-testid="exception-actions"><span className="eyebrow">STATUS</span><div className="status-buttons">{STATUSES.map((s) => <button key={s} className={`mini-button ${focused?.exception_status === s ? "active" : ""}`} data-testid={`set-status-${s}`} disabled={mut.isPending || focused?.exception_status === s} onClick={() => focused && setStatus(focused, s)}>{STATUS_LABELS[s]}</button>)}</div>
          <span className="eyebrow">ADD NOTE</span><div className="note-form"><textarea data-testid="exception-note-input" rows={2} placeholder="Record the follow-up made, evidence obtained or reason for ignoring…" value={note} onChange={(e) => setNote(e.target.value)} /><button className="secondary-button" data-testid="add-note-button" disabled={!note.trim() || mut.isPending} onClick={() => focused && mut.mutate({ id: focused.id, body: { note } })}><MessageSquarePlus size={14} /> Add note</button></div>
          <span className="eyebrow">ASSIGN</span><div className="note-form inline"><input data-testid="exception-assignee-input" placeholder="Assignee name" value={assignee} onChange={(e) => setAssignee(e.target.value)} /><button className="secondary-button" data-testid="assign-button" disabled={mut.isPending} onClick={() => focused && mut.mutate({ id: focused.id, body: { assignee } })}><UserPlus size={14} /> Assign</button></div>
          {!!focused?.notes?.length && <div className="notes-block"><span className="eyebrow">NOTES</span>{focused.notes.map((n, i) => <div className="note" key={i}><p>{n.text}</p><small>{n.by} · {fmtDateTime(n.at)}</small></div>)}</div>}</div>
        <ResultDetail id={focus} onNavigate={() => setFocus(null)} />
      </>}
    </Drawer>
  </>;
}
