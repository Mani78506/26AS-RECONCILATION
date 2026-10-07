import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Bot, History, Save, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Amount, ClaimBadge, ErrorNotice, Field, IdentityBadge, MethodBadge, ResultBadge, SeverityBadge, Skeleton, StatusBadge } from "./common";
import { fmtDate, fmtDateTime, inr, METHOD_LABELS, pct, RESULT_LABELS } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import type { ReconciliationResult, RelationshipCommentary } from "../types";
import { useWorkspace } from "../context/WorkspaceContext";
import { getReconciliationStatusPresentation } from "../lib/reconciliationStatus";

export function ResultDetail({ id, showPan = true, onNavigate }: { id: string; showPan?: boolean; onNavigate?: () => void }) {
  const navigate = useNavigate();
  const { runId, run, user } = useWorkspace();
  const q = useQuery({ queryKey: ["result", id, runId], queryFn: () => api.result(id, runId), enabled: !!runId });
  if (q.isLoading) return <div className="detail-loading" data-testid="detail-loading">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} h={14} w={`${50 + (i % 3) * 15}%`} />)}</div>;
  if (q.isError || !q.data) return <ErrorNotice message={getErrorMessage(q.error, "This transaction could not be loaded.")} onRetry={() => q.refetch()} />;
  const r: ReconciliationResult = q.data;
  const b = r.books;
  const analysisOnly = r.analysis_mode === "26AS_ONLY" || run?.workflow === "26AS_ONLY";
  const groupSize = r.group_size;
  const isGroup = !!r.match_group_id;
  const statusPresentation = analysisOnly
    ? { label: RESULT_LABELS[r.analysis_status || r.result] || "26AS analysis", explanation: r.reason || "No calculation reason was stored.", action: r.recommended_action || "Review the 26AS entry and configured-rule inputs." }
    : getReconciliationStatusPresentation(r);
  const calculation = b || r.statement_entries?.[0];
  return <div className="result-detail" data-testid="result-detail">
    <div className="detail-badges"><ResultBadge result={r.analysis_status || r.result} />{!analysisOnly && <><ClaimBadge value={r.claimability} /><MethodBadge value={r.match_method} /><IdentityBadge value={r.identity_status} /></>}{r.severity !== "NONE" && <SeverityBadge value={r.severity} />}{!analysisOnly && r.exception_state && r.result !== "MATCHED_CLAIMABLE" && <StatusBadge value={r.exception_state.status} />}</div>
    <section className="detail-section detail-client-context"><span className="eyebrow">ASSESSEE</span><div className="detail-fields compact"><Field label="Assessee">{run?.assessee_name || "Unknown assessee"}</Field><Field label="Assessee PAN" mono>{run?.assessee_pan || "—"}</Field><Field label="Financial year">{r.financial_year || run?.financial_year || "—"}</Field>{analysisOnly && <Field label="Source">26AS / Form 16A</Field>}</div></section>
    <div className="detail-kpis"><div><span>{analysisOnly ? "TDS EXPECTED (CALCULATED)" : "BOOKS TDS EXPECTED"}</span><b data-testid="detail-tds-expected">{inr(isGroup ? r.books_group_total : r.tds_expected)}</b>{isGroup && <small>group total</small>}</div><div><span>26AS TDS DEDUCTED</span><b data-testid="detail-tax-deducted">{inr(r.tax_deducted)}</b>{isGroup && <small>group total</small>}</div><div><span>{analysisOnly ? "DIFFERENCE" : "DIFFERENCE (EXPECTED − DEDUCTED)"}</span><b data-testid="detail-difference"><Amount value={r.difference} signed strong /></b><small>{pct(r.difference_pct)}</small></div><div><span>{analysisOnly ? "ANALYSIS STATUS" : "GROUP SIZE"}</span><b data-testid="detail-group-size">{analysisOnly ? (r.analysis_status || "—").replaceAll("_", " ") : groupSize}</b><small>{analysisOnly ? "No Books reconciliation" : r.match_group_id || "no group"}</small></div></div>

    {calculation && <section className="detail-section" data-testid="tds-calculation"><span className="eyebrow">TDS CALCULATION</span><div className="detail-fields compact"><Field label="Expected TDS">{calculation.tds_expected == null ? "Not Determinable" : inr(calculation.tds_expected)}</Field><Field label="Method">{calculation.tds_calculation_method?.replaceAll("_", " ") || "Not calculated"}</Field><Field label="Status">{calculation.tds_calculation_status?.replaceAll("_", " ") || "—"}</Field><Field label="Section" mono>{calculation.section || "—"}</Field><Field label="Rate">{calculation.tds_rate == null ? "—" : `${calculation.tds_rate}%`}</Field><Field label="Calculation basis">{calculation.tds_calculation_basis || "—"}</Field><Field label="Rule ID" mono>{calculation.tds_rule_id || "—"}</Field><Field label="Rule version">{calculation.tds_rule_version ?? "—"}</Field><Field label="Rule source">{calculation.tds_rule_source || "—"}</Field><Field label="Effective dates">{calculation.tds_rule_effective_from && calculation.tds_rule_effective_to ? `${fmtDate(calculation.tds_rule_effective_from)} – ${fmtDate(calculation.tds_rule_effective_to)}` : "—"}</Field></div><p className="muted-copy">{calculation.tds_calculation_reason || "No calculation reason was stored."}</p></section>}

    {!analysisOnly && <section className="detail-section" data-testid="detail-books"><div className="section-heading"><div><span className="eyebrow">BOOKS</span><h3>{b ? "Source transaction" : "No books transaction"}</h3></div>{b && <span className="quiet-id mono">{b.transaction_id}</span>}</div>
      {b ? <div className="detail-fields"><Field label="Transaction ID" mono>{b.transaction_id}</Field><Field label="Books Customer">{b.customer_name}</Field><Field label="Books Customer Code" mono>{b.customer_code}</Field><Field label="Party PAN" mono testId="detail-party-pan">{showPan ? b.party_pan || "—" : b.party_pan ? `${b.party_pan.slice(0, 5)}****${b.party_pan.slice(9)}` : "—"}</Field><Field label="Party GSTIN" mono>{b.party_gstin || "—"}</Field><Field label="Document number" mono>{b.document_number || "—"}</Field><Field label="Document date">{fmtDate(b.document_date)}</Field><Field label="Taxable value">{inr(b.taxable_value)}</Field><Field label="GST value">{inr(b.gst_value)}</Field><Field label="TDS expected">{inr(b.tds_expected)}</Field><Field label="Section" mono>{b.section || "—"}</Field><Field label="Advance">{b.advance ? "Yes" : "No"}</Field><Field label="Financial year">{b.financial_year}</Field><Field label="Quarter">{b.quarter}</Field></div>
        : <p className="muted-copy">This row originates from 26AS. {r.result === "IDENTITY_UNMAPPED" ? "No authoritative Books Customer mapping is available for this deductor, so no Books transaction can be associated." : "No Books transaction remained to absorb this entry."}</p>}
      {r.group_rows && r.group_rows.length > 1 && <div className="group-rows" data-testid="detail-group-rows"><span className="eyebrow">MATCHED BOOKS TRANSACTIONS ({r.group_rows.length})</span>{r.group_rows.map((g) => <button key={g.id} className={`group-row ${g.id === r.id ? "current" : ""}`} data-testid={`group-row-${g.transaction_id}`} onClick={() => { onNavigate?.(); navigate(`/reconciliation/${encodeURIComponent(g.id)}`); }}><b className="mono">{g.transaction_id}</b><span>{fmtDate(g.books_date)} · {g.books_quarter} · {g.section || "—"}</span><Amount value={g.tds_expected} /></button>)}</div>}
    </section>}

    <section className="detail-section" data-testid="detail-statement"><div className="section-heading"><div><span className="eyebrow">26AS / DEDUCTOR</span><h3>{r.statement_entries?.length ? `Statement entr${r.statement_entries.length === 1 ? "y" : "ies"}` : "No 26AS entry"}</h3></div>{!!r.statement_entries?.length && <span className="quiet-id">{r.statement_entries.length} entr{r.statement_entries.length === 1 ? "y" : "ies"} · {r.matched_tans.length} TAN{r.matched_tans.length === 1 ? "" : "s"}</span>}</div>
      {r.statement_entries?.length ? r.statement_entries.map((s) => <div className="statement-card" key={s.statement_id} data-testid={`statement-entry-${s.statement_id}`}><div className="detail-fields compact"><Field label="TAN" mono>{s.tan}</Field><Field label="Deductor / Payer">{s.deductor_name}</Field><Field label="Transaction date">{fmtDate(s.transaction_date)}</Field><Field label="Amount paid / credited">{inr(s.amount_paid)}</Field><Field label="Tax deducted">{inr(s.tax_deducted)}</Field>{analysisOnly && <><Field label="TDS expected">{s.tds_expected == null ? "Not Determinable" : inr(s.tds_expected)}</Field><Field label="Difference"><Amount value={r.difference} signed /></Field></>}<Field label="TDS deposited">{inr(s.tds_deposited)}</Field><Field label="Status">{s.status} · {s.status_label}</Field><Field label="Section" mono>{s.section || "—"}</Field><Field label="Financial year">{s.financial_year}</Field><Field label="Quarter">{s.quarter}</Field></div></div>)
        : <p className="muted-copy">{r.result === "MISSING_IN_26AS" ? "No 26AS entry from any mapped TAN could be matched to this Books transaction." : "No statement entry."}</p>}
    </section>

    <section className="detail-section explanation" data-testid="detail-explanation"><span className="eyebrow">{analysisOnly ? "26AS ANALYSIS" : "SYSTEM FINDING"}</span><h3>{statusPresentation.label}</h3>
      <div className="explanation-copy" data-testid="detail-reason">{statusPresentation.explanation}</div>{r.status && <div className="detail-status-note"><b>26AS Status: {r.status}</b>{r.result === "MATCHED_NOT_CLAIMABLE" && <span>This stored status is why the matched credit is not currently claimable.</span>}</div>}
      {analysisOnly ? <div className="totals"><div className="total-line"><span>Analysis method</span><b>Configured-rule analysis</b></div><div className="total-line"><span>TDS deducted</span><b>{inr(r.tax_deducted)}</b></div><div className="total-line"><span>TDS expected</span><b>{r.tds_expected == null ? "Not Determinable" : inr(r.tds_expected)}</b></div><div className="total-line"><span>Difference</span><b><Amount value={r.difference} signed /></b></div></div> : <div className="totals"><div className="total-line"><span>Match method</span><b>{METHOD_LABELS[r.match_method]}</b></div><div className="total-line"><span>Books TDS expected (group)</span><b>{inr(r.books_group_total)}</b></div><div className="total-line"><span>26AS TDS deducted (group)</span><b>{inr(r.statement_group_total)}</b></div><div className="total-line"><span>Difference (expected − deducted)</span><b><Amount value={r.difference} signed /></b></div><div className="total-line"><span>Difference %</span><b>{pct(r.difference_pct)}</b></div><div className="total-line"><span>Matched books transactions</span><b className="mono">{r.matched_books_ids.join(", ") || "—"}</b></div><div className="total-line"><span>Matched 26AS TANs</span><b className="mono">{r.matched_tans.join(", ") || "—"}</b></div><div className="total-line"><span>Match group ID</span><b className="mono">{r.match_group_id || "—"}</b></div></div>}
    </section>

    {!analysisOnly && runId && <CommentaryPanel runId={runId} relationshipId={r.relationship_id || r.match_group_id || r.id} current={r.commentary || null} reviewer={user.name} />}

    <section className="detail-section result-block" data-testid="detail-result"><span className="eyebrow">{analysisOnly ? "ANALYSIS" : "RESULT"}</span><div className="result-line"><b>{RESULT_LABELS[r.analysis_status || r.result]}</b>{!analysisOnly && <ClaimBadge value={r.claimability} />}</div>
      <button className="secondary-button ai-explain" data-testid="detail-ask-ai-button" onClick={() => window.dispatchEvent(new CustomEvent("open-ai-assistant", { detail: { resultId: r.id, runId: r.run_id } }))}><Bot size={14} /> Explain with AI</button>
      <div className="recommended" data-testid="detail-recommended-action"><div className="recommend-icon"><Sparkles size={15} /></div><div><span>RECOMMENDED CA ACTION</span><p>{statusPresentation.action}</p></div></div>
      {r.exception_state && r.result !== "MATCHED_CLAIMABLE" && <div className="notes-block" data-testid="detail-notes"><span className="eyebrow">REVIEW TRAIL</span>{r.exception_state.notes.length ? r.exception_state.notes.map((n, i) => <div className="note" key={i}><p>{n.text}</p><small>{n.by} · {fmtDateTime(n.at)}</small></div>) : <small className="muted-copy">No notes yet.</small>}<button className="text-button" data-testid="detail-open-exception-button" onClick={() => { onNavigate?.(); navigate(`/exceptions?focus=${encodeURIComponent(r.id)}`); }}>Manage in Exception Workbench <ArrowRight size={13} /></button></div>}
    </section>
  </div>;
}

function CommentaryPanel({ runId, relationshipId, current, reviewer }: { runId: string; relationshipId: string; current: RelationshipCommentary | null; reviewer: string }) {
  const qc = useQueryClient();
  const [commentary, setCommentary] = useState(current?.commentary || "");
  const [status, setStatus] = useState<"NEW" | "REVIEWED">(current?.review_status || "NEW");
  const [showHistory, setShowHistory] = useState(false);
  useEffect(() => { setCommentary(current?.commentary || ""); setStatus(current?.review_status || "NEW"); }, [current?.commentary_id]);
  const history = useQuery({ queryKey: ["relationship-commentary-history", runId, relationshipId], queryFn: () => api.relationshipCommentaryHistory(runId, relationshipId), enabled: showHistory });
  const save = useMutation({
    mutationFn: () => current
      ? api.updateRelationshipCommentary(runId, relationshipId, { commentary, review_status: status, reviewer })
      : api.createRelationshipCommentary(runId, relationshipId, { commentary, review_status: status, reviewer }),
    onSuccess: () => {
      toast.success(current ? "CA commentary updated" : "CA commentary saved");
      qc.invalidateQueries({ queryKey: ["result"] });
      qc.invalidateQueries({ queryKey: ["results"] });
      qc.invalidateQueries({ queryKey: ["relationship-commentary-history", runId, relationshipId] });
    },
    onError: (error) => toast.error(getErrorMessage(error, "The CA commentary could not be saved.")),
  });
  const canSave = commentary.trim().length > 0 && commentary.trim().length <= 2000 && !!reviewer;
  return <section className="detail-section ca-commentary" data-testid="relationship-commentary">
    <div className="section-heading"><div><span className="eyebrow">CA COMMENTARY</span><h3>Working-paper note</h3><p className="muted-copy">This note records the CA review only. It does not change the system finding, matching result, amounts, or claimability.</p></div>{current && <span className={`badge ${status === "REVIEWED" ? "green" : "amber"}`}>{status === "REVIEWED" ? "Reviewed" : "New commentary"}</span>}</div>
    <textarea data-testid="relationship-commentary-input" value={commentary} maxLength={2000} placeholder="Record the review conclusion, evidence considered, and follow-up required." onChange={(event) => setCommentary(event.target.value)} aria-label="CA commentary" />
    <div className="commentary-actions"><label>Review status<select data-testid="relationship-commentary-status" value={status} onChange={(event) => setStatus(event.target.value as "NEW" | "REVIEWED")}><option value="NEW">New</option><option value="REVIEWED">Reviewed</option></select></label><small>{commentary.length}/2000 characters</small><button className="primary-button" data-testid="save-relationship-commentary" disabled={!canSave || save.isPending} onClick={() => save.mutate()}><Save size={14} />{save.isPending ? "Saving…" : current ? "Save new revision" : "Save commentary"}</button></div>
    {current && <div className="commentary-provenance"><span>Current revision {current.version}</span><span>Created by <b>{current.created_by}</b> · {fmtDateTime(current.created_at)}</span><span>Last updated by <b>{current.updated_by}</b> · {fmtDateTime(current.updated_at)}</span><button className="text-button" onClick={() => setShowHistory((open) => !open)}><History size={13} />{showHistory ? "Hide history" : "View history"}</button></div>}
    {showHistory && <div className="commentary-history" data-testid="relationship-commentary-history">{history.isLoading ? <small className="muted-copy">Loading commentary history…</small> : history.data?.items.map((item) => <article key={item.commentary_id}><div><b>Revision {item.version}</b><span className={`badge ${item.review_status === "REVIEWED" ? "green" : "amber"}`}>{item.review_status === "REVIEWED" ? "Reviewed" : "New"}</span></div><p>{item.commentary}</p><small>{item.updated_by} · {fmtDateTime(item.updated_at)}</small></article>)}</div>}
  </section>;
}
