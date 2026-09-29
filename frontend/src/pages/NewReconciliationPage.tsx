import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Check, CheckCircle2, CircleDashed, Database, Download, FileCheck2, FileSpreadsheet, Loader2, RefreshCw, Replace, Upload, X, XCircle } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, ErrorNotice, InfoNotice, PageHeader } from "../components/common";
import { useWorkspace } from "../context/WorkspaceContext";
import { fileSize, FILE_LABELS } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import { WORKFLOW_UPLOAD_KINDS, workflowRequirement, workflowSourceIds, workflowUploadsReady } from "../lib/workflow";
import type { FileKind, FileValidation, Job, StepState, UploadInfo, ValidationResponse, Workflow, SemanticProfile } from "../types";

const ACCEPT = ".csv,.xlsx,.xls";
const PDF_ACCEPT = ".csv,.xlsx,.xls,.pdf";
const KINDS: { kind: FileKind; title: string; description: string; icon: React.ElementType; required: boolean }[] = [
  { kind: "books", title: "Books", description: "Client's Books / ERP export of receipts with TDS receivable.", icon: FileSpreadsheet, required: true },
  { kind: "form26as", title: "26AS / Form 16A", description: "Assessee's tax credit statement (TAN-wise deductor entries). Text-based PDF supported.", icon: BookOpen, required: true },
  { kind: "customer_master", title: "Customer Master", description: "Trusted customer â†’ PAN / GSTIN / TAN mapping. Optional but recommended.", icon: Database, required: false },
  { kind: "tds_receivable", title: "TDS Expected / Receivable", description: "Reported party-wise TDS Expected / Receivable, compared with 26AS TDS Deducted.", icon: FileCheck2, required: true },
  { kind: "sales_registry", title: "Sales Registry", description: "Customer sales amounts, compared with 26AS amount credited / paid.", icon: Database, required: true },
];
type UploadState = { file?: File; info?: UploadInfo; progress: number; status: "idle" | "uploading" | "processing" | "done" | "error"; error?: string };
const emptyUploads = (): Record<FileKind, UploadState> => ({ books: { progress: 0, status: "idle" }, form26as: { progress: 0, status: "idle" }, customer_master: { progress: 0, status: "idle" }, tds_receivable: { progress: 0, status: "idle" }, sales_registry: { progress: 0, status: "idle" }, payment_ledger: { progress: 0, status: "idle" }, tds_deposit_evidence: { progress: 0, status: "idle" } });
const UPLOAD_DRAFT_KEY = "26as-reconciliation-upload-draft-v1";

export default function NewReconciliationPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { watchJob, selectRun, clientId, run, processing: globalProcessing } = useWorkspace();
  // A new-client draft must be blank, but opening it must not discard the
  // completed workspace the CA was reviewing. The existing run remains
  // available when the CA returns to Dashboard.
  const newClientDraft = searchParams.get("new_client") === "1";
  const draftClientId = newClientDraft ? null : clientId;
  const [uploads, setUploads] = useState<Record<FileKind, UploadState>>(emptyUploads);
  const [meta, setMeta] = useState({ assessee_name: "", assessee_pan: "", financial_year: "" });
  const [workflow, setWorkflow] = useState<Workflow>("FULL_RECONCILIATION");
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [validationId, setValidationId] = useState(() => new URLSearchParams(window.location.search).get("validation"));
  const [validationJob, setValidationJob] = useState<any>(null);
  const validationSource = useRef<any>(null);
  const rememberValidation = useCallback((id: string | null) => {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("validation", id); else url.searchParams.delete("validation");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    setValidationId(id);
  }, []);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completedRedirectRef = useRef<string | null>(null);
  const clientDirectory = useQuery({ queryKey: ["workspace-clients"], queryFn: api.clients, enabled: !!draftClientId, staleTime: 15_000 });
  const selectedClient = clientDirectory.data?.find((client) => client.client_id === draftClientId);
  const selectedClientReady = !draftClientId || !!selectedClient;

  // A persisted draft belongs to one selected client only. A CA choosing
  // “New client reconciliation” must never see source files from the prior
  // client, even though those upload IDs remain valid on the server.
  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(UPLOAD_DRAFT_KEY) || "{}");
      if (newClientDraft || saved.workflow === "SALES_TDS_26AS" || !saved.uploads || saved.clientId !== draftClientId) { sessionStorage.removeItem(UPLOAD_DRAFT_KEY); setUploads(emptyUploads()); return; }
      setUploads((current) => Object.fromEntries(Object.entries(current).map(([kind, state]) => {
        const info = saved.uploads[kind] as UploadInfo | undefined;
        if (!info?.upload_id) return [kind, state];
        return [kind, { progress: 100, info, status: info.source_processing_status === "COMPLETED" ? "done" : info.source_processing_status === "FAILED" ? "error" : "processing", error: info.source_processing_error || undefined }];
      })) as Record<FileKind, UploadState>);
    } catch { sessionStorage.removeItem(UPLOAD_DRAFT_KEY); }
  }, [draftClientId, newClientDraft]);

  useEffect(() => {
    if (newClientDraft || workflow === "SALES_TDS_26AS") { sessionStorage.removeItem(UPLOAD_DRAFT_KEY); return; }
    const saved = Object.fromEntries(Object.entries(uploads).flatMap(([kind, state]) => state.info ? [[kind, state.info]] : []));
    if (Object.keys(saved).length) sessionStorage.setItem(UPLOAD_DRAFT_KEY, JSON.stringify({ workflow, clientId: draftClientId, uploads: saved })); else sessionStorage.removeItem(UPLOAD_DRAFT_KEY);
  }, [uploads, workflow, draftClientId, newClientDraft]);

  const processingIds = Object.values(uploads).filter((state) => state.status === "processing" && state.info?.upload_id).map((state) => state.info!.upload_id).join(",");
  useEffect(() => {
    if (!processingIds) return;
    let cancelled = false;
    const refresh = async () => {
      const current = await Promise.all(processingIds.split(",").map(async (id) => {
        try { return await api.uploadStatus(id); } catch { return null; }
      }));
      if (cancelled) return;
      setUploads((states) => {
        const next = { ...states };
        for (const info of current) {
          if (!info) continue;
          const kind = info.kind;
          if (info.source_processing_status === "COMPLETED") next[kind] = { ...next[kind], info, progress: 100, status: "done", error: undefined };
          else if (info.source_processing_status === "FAILED") next[kind] = { ...next[kind], info, progress: 100, status: "error", error: info.source_processing_error || "Source processing failed. Replace the file and try again." };
          else next[kind] = { ...next[kind], info, progress: 100, status: "processing" };
        }
        return next;
      });
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [processingIds]);

  useEffect(() => {
    if (newClientDraft) { setMeta({ assessee_name: "", assessee_pan: "", financial_year: "" }); return; }
    if (!selectedClient) return;
    setMeta((current) => current.assessee_name || current.assessee_pan ? current : { ...current, assessee_name: selectedClient.client_name, assessee_pan: selectedClient.assessee_pan });
  }, [newClientDraft, selectedClient]);

  const source = () => ({
    ...workflowSourceIds(workflow, Object.fromEntries(Object.entries(uploads).map(([kind, state]) => [kind, state.info?.upload_id]))),
    // A run started from a client workspace must retain that exact client
    // boundary even if the directory query finishes after this page renders.
    assessee_name: (selectedClient?.client_name ?? meta.assessee_name).trim(), assessee_pan: (selectedClient?.assessee_pan ?? meta.assessee_pan).trim().toUpperCase(),
    client_id: selectedClient?.client_id, financial_year: meta.financial_year.trim(), workflow,
  });
  const effectivePan = selectedClient?.assessee_pan ?? meta.assessee_pan;
  const panValid = !effectivePan || /^[A-Z]{5}[0-9]{4}[A-Z]$/i.test(effectivePan.trim());
  const fyValid = !meta.financial_year || /^\d{4}-\d{2}$/.test(meta.financial_year.trim());
  // Validation is based on completed upload IDs, never merely the local file
  // name shown on a card.  Keep each workflow's source requirements isolated.
  const sourcesReady = workflowUploadsReady(workflow, Object.fromEntries(Object.entries(uploads).map(([kind, state]) => [kind, state.info?.upload_id])));
  const sourcesProcessed = KINDS.filter((item) => item.required && WORKFLOW_UPLOAD_KINDS[workflow].includes(item.kind)).every((item) => uploads[item.kind].status === "done");
  const ready = sourcesReady && sourcesProcessed && selectedClientReady && panValid && fyValid;
  const validationRequirement = workflowRequirement(workflow);
  // The process screen is run-backed, not only component-local. It therefore
  // survives refresh/navigation while the selected reconciliation is running.
  const activeJob = useQuery({ queryKey: ["processing-job", run?.run_id], queryFn: () => api.job(run!.run_id), enabled: !job && run?.status === "PROCESSING", refetchInterval: run?.status === "PROCESSING" ? 1200 : false });
  const displayedJob = job || activeJob.data || null;

  useEffect(() => {
    if (!validationId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setValidating(true);
    const poll = async () => {
      try {
        const current = await api.booksValidation(validationId);
        if (cancelled) return;
        setValidationJob(current);
        validationSource.current = current.source;
        if (current.status === "COMPLETED" && current.result) {
          setValidation(current.result); setStep(2); setValidating(false); setError("");
          return;
        }
        if (current.status === "FAILED") { setError(current.error || "Validation failed."); setValidating(false); return; }
        setError("");
      } catch { if (!cancelled) setError("Reconnecting to validation. The job is retained; reload to reconnect if needed."); }
      if (!cancelled) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [validationId]);

  useEffect(() => {
    if (!job && run?.status === "PROCESSING") setStep(3);
  }, [job, run?.run_id, run?.status]);

  // Completion is terminal for this wizard. Select the exact completed run
  // first, then take the CA straight to its reconciliation results. The ref
  // prevents duplicate navigation from the local and workspace pollers.
  useEffect(() => {
    if (displayedJob?.status !== "COMPLETED") return;
    const completedRunId = displayedJob.run_id || displayedJob.job_id;
    if (!completedRunId || completedRedirectRef.current === completedRunId) return;
    completedRedirectRef.current = completedRunId;
    selectRun(completedRunId);
    navigate("/reconciliation");
  }, [displayedJob?.status, displayedJob?.run_id, displayedJob?.job_id, navigate, selectRun]);

  const pick = useCallback(async (kind: FileKind, file: File | null) => {
    if (!file) return;
    rememberValidation(null); setValidationJob(null); validationSource.current = null;
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (![".csv", ".xlsx", ".xls", ...(kind === "form26as" ? [".pdf"] : [])].includes(ext)) { setUploads((u) => ({ ...u, [kind]: { file, progress: 0, status: "error", error: kind === "form26as" ? `Unsupported file type ${ext || ""}. Upload CSV, XLSX, XLS or text-based PDF.` : `Unsupported file type ${ext || ""}. Upload CSV, XLSX or XLS.` } })); return; }
    if (file.size > 25 * 1024 * 1024) { setUploads((u) => ({ ...u, [kind]: { file, progress: 0, status: "error", error: "File exceeds the 25 MB limit." } })); return; }
    const other = (Object.entries(uploads) as [FileKind, UploadState][]).find(([k, v]) => k !== kind && v.file && v.file.name === file.name && v.file.size === file.size);
    if (other) toast.warning(`This file is already uploaded as ${FILE_LABELS[other[0]]}. Make sure you selected the right file.`);
    const previous = uploads[kind].info?.upload_id;
    setUploads((u) => ({ ...u, [kind]: { file, progress: 0, status: "uploading" } }));
    setValidation(null); setStep(1);
    try {
      const info = await api.upload(kind, file, (p) => setUploads((u) => ({ ...u, [kind]: { ...u[kind], progress: p } })));
      const complete = info.source_processing_status === "COMPLETED";
      setUploads((u) => ({ ...u, [kind]: { file, info, progress: 100, status: complete ? "done" : "processing" } }));
      if (previous) api.deleteUpload(previous).catch(() => undefined);
    } catch (e) {
      setUploads((u) => ({ ...u, [kind]: { file, progress: 0, status: "error", error: getErrorMessage(e, "Upload failed. Check the connection and try again.") } }));
    }
  }, [uploads, rememberValidation]);

  const remove = (kind: FileKind) => { const id = uploads[kind].info?.upload_id; if (id) api.deleteUpload(id).catch(() => undefined); rememberValidation(null); setValidationJob(null); validationSource.current = null; setUploads((u) => ({ ...u, [kind]: { progress: 0, status: "idle" } })); setValidation(null); setStep(1); };
  const selectWorkflow = (next: Workflow) => {
    if (next === workflow) return;
    rememberValidation(null); setValidationJob(null); validationSource.current = null; setValidating(false);
    if (next === "SALES_TDS_26AS") sessionStorage.removeItem(UPLOAD_DRAFT_KEY);
    setWorkflow(next);
    setUploads(emptyUploads());
    setValidation(null);
    setStep(1);
  };

  const validate = async () => {
    if (!ready) return;
    setValidating(true); setError("");
    if (workflow === "FULL_RECONCILIATION") {
      const id = crypto.randomUUID();
      try {
        const current = await api.startBooksValidation(id, source());
        setValidationJob(current); validationSource.current = source(); rememberValidation(id);
      } catch (e) { setError(getErrorMessage(e)); setValidating(false); }
      return;
    }
    try { const res = await api.validate(source()); setValidation(res); setStep(2); if (res.status === "BLOCKED") toast.error("Validation blocked", { description: "Fix the structural source issues and validate again." }); }
    catch (e) { setError(getErrorMessage(e)); }
    finally { setValidating(false); }
  };

  const start = async () => {
    if (!validation?.can_process) return;
    setStarting(true); setError("");
    try { const j = await api.reconcile(validationSource.current || source()); rememberValidation(null); completedRedirectRef.current = null; setJob(j); setStep(3); watchJob(j.job_id); }
    catch (e) { setError(getErrorMessage(e)); }
    finally { setStarting(false); }
  };

  useEffect(() => {
    if (!job || job.status !== "PROCESSING") return;
    pollRef.current = setInterval(async () => { try { const j = await api.job(job.job_id); setJob(j); if (j.status !== "PROCESSING" && pollRef.current) clearInterval(pollRef.current); } catch { /* retry */ } }, 1200);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [job?.job_id, job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const visibleKinds = KINDS.filter((k) => WORKFLOW_UPLOAD_KINDS[workflow].includes(k.kind));
  return <>
    <PageHeader eyebrow="SOURCE INGESTION" title={workflow === "26AS_ONLY" ? "26AS-Only TDS Analysis" : "New Reconciliation"} subtitle={workflow === "26AS_ONLY" ? "Analyse reported TDS against an independently configured calculation rule." : "Upload source files, validate them, then run the reconciliation engine."} action={<div className="template-links"><span>Templates:</span>{visibleKinds.map((k) => <a key={k.kind} className="text-button" data-testid={`template-${k.kind}-link`} href={api.templateUrl(k.kind)} download><Download size={12} /> {k.title}</a>)}</div>} />
    <ol className="step-line" data-testid="step-line">{["Upload sources", "Validate files", "Process & review"].map((label, i) => <li key={label} className={`step ${step === i + 1 ? "active" : ""} ${step > i + 1 ? "done" : ""}`} data-testid={`step-${i + 1}`}><b>{step > i + 1 ? <Check size={12} /> : `0${i + 1}`}</b><span>{label}</span></li>)}</ol>
    {validationJob && validating && <section className="workspace-section" role="status" data-testid="books-validation-progress"><span className="eyebrow">RECONCILIATION STARTED</span><h2>Validating Books + 26AS</h2><p>Run ID: <b className="mono">{validationJob.job_id}</b></p><p>Status: <b>{validationJob.status}</b></p><p>Processed: <b>{Number(validationJob.processed_rows || 0).toLocaleString()} / {Number(validationJob.total_rows || 0).toLocaleString()}</b> rows</p><p className="muted">The validation continues in the background. It is safe to refresh this page.</p></section>}

    {step !== 3 && <>
      <section className="workspace-section assessee-form" data-testid="workflow-choice"><div className="section-heading"><div><span className="eyebrow">RECONCILIATION WORKFLOW</span><h2>Choose the work to perform</h2></div></div><div className="workflow-options" role="radiogroup" aria-label="Reconciliation workflow"><button type="button" role="radio" aria-checked={workflow === "FULL_RECONCILIATION"} className={workflow === "FULL_RECONCILIATION" ? "primary-button" : "secondary-button"} onClick={() => selectWorkflow("FULL_RECONCILIATION")}><FileSpreadsheet size={15} /> Books + 26AS Reconciliation</button><button type="button" role="radio" aria-checked={workflow === "26AS_ONLY"} className={workflow === "26AS_ONLY" ? "primary-button" : "secondary-button"} onClick={() => selectWorkflow("26AS_ONLY")}><BookOpen size={15} /> 26AS-Only TDS Analysis</button><button type="button" role="radio" aria-checked={workflow === "SALES_TDS_26AS"} className={workflow === "SALES_TDS_26AS" ? "primary-button" : "secondary-button"} onClick={() => selectWorkflow("SALES_TDS_26AS")}><Database size={15} /> Sales + TDS + 26AS</button></div><p className="form-hint">{workflow === "26AS_ONLY" ? "No Books match is implied. Expected TDS is calculated only with sufficient fields and an authoritative configured rule." : workflow === "SALES_TDS_26AS" ? "Compare Sales Registry amount with 26AS Amount Credited/Paid and TDS Expected / Receivable with 26AS TDS Deducted. Customer Master is not required." : "Compare client Books expected TDS with actual TDS reported in 26AS."}</p></section>
      <section className="workspace-section assessee-form" data-testid="assessee-form"><div className="section-heading"><div><span className="eyebrow">ASSESSEE</span><h2>{workflow === "26AS_ONLY" ? "Whose 26AS is being analysed?" : "Whose 26AS is being reconciled?"}</h2></div></div>
        <div className="form-grid"><label>Assessee / client name<input data-testid="assessee-name-input" value={selectedClient?.client_name ?? meta.assessee_name} onChange={(e) => setMeta({ ...meta, assessee_name: e.target.value })} placeholder="e.g. Meridian Consulting LLP" readOnly={!!selectedClient} /></label><label>Assessee PAN<input data-testid="assessee-pan-input" className="mono" value={selectedClient?.assessee_pan ?? meta.assessee_pan} onChange={(e) => setMeta({ ...meta, assessee_pan: e.target.value.toUpperCase() })} placeholder="AAAAA9999A" maxLength={10} aria-invalid={!panValid} readOnly={!!selectedClient} />{!panValid && <small className="field-error" data-testid="assessee-pan-error">PAN must be 5 letters, 4 digits, 1 letter.</small>}</label><label>Financial year<input data-testid="financial-year-input" value={meta.financial_year} onChange={(e) => setMeta({ ...meta, financial_year: e.target.value })} placeholder="2024-25" aria-invalid={!fyValid} />{!fyValid && <small className="field-error" data-testid="fy-error">Use the format 2024-25.</small>}</label></div>
        {clientId && !selectedClientReady ? <InfoNotice tone="info">Loading the selected client before this reconciliation can be started.</InfoNotice> : selectedClient ? <InfoNotice tone="info">This reconciliation will be saved to <b>{selectedClient.client_name}</b> and will appear in that client’s completed-run history.</InfoNotice> : null}
        <p className="form-hint">The assessee PAN identifies whose 26AS this is. Deductor TANs in 26AS are mapped to customers via the Customer Master, saved mappings and name signals â€” never derived from PAN.</p></section>
      <section className="upload-grid">{visibleKinds.map((k) => <UploadCard key={k.kind} {...k} state={uploads[k.kind]} onPick={(f) => pick(k.kind, f)} onRemove={() => remove(k.kind)} disabled={validating || starting} />)}</section>
      {error && <ErrorNotice message={error} onRetry={step === 2 && validation ? start : validate} />}
      <div className="validation-callout" data-testid="validate-callout"><div className="callout-icon"><FileCheck2 size={18} /></div><div><b>{ready ? "Sources ready â€” validate before processing" : validationRequirement}</b><p>Validation checks columns, dates, amounts, financial year and identifiers. Nothing is reconciled until validation passes.</p></div><button className="primary-button" data-testid="validate-files-button" disabled={!ready || validating} onClick={validate}>{validating ? <><Loader2 size={15} className="spin" /> Validatingâ€¦</> : <>Validate files <ArrowRight size={15} /></>}</button></div>
    </>}

    {step === 2 && validation && <section className="validation-results" data-testid="validation-results">
      <div className="section-heading"><div><span className="eyebrow">VALIDATION</span><h2>{validation.status === "VALID" ? "All files are ready" : validation.status === "READY_WITH_WARNINGS" ? "Validation completed with warnings" : validation.status === "READY_WITH_EXCEPTIONS" ? "Validation completed with exceptions" : "Validation blocked"}</h2></div><span className={`badge ${validation.status === "VALID" ? "green" : validation.status === "BLOCKED" ? "red" : "amber"}`} data-testid="validation-status">{validation.status}</span></div>
      <div className="validation-grid">{validation.files.map((f) => <ValidationCard key={f.kind} file={f} />)}</div>
      <section className="workspace-section source-understanding-section" data-testid="source-understanding"><div className="section-heading"><div><span className="eyebrow">SOURCE UNDERSTANDING</span><h3>Source structure and reconciliation readiness</h3><p className="muted">The system keeps accounting movements separate from TDS amounts. Review is requested only where source evidence is incomplete.</p></div></div>{validation.files.filter((f) => f.ledger_analysis).map((f) => <SourceUnderstanding key={f.kind} label={f.label} profile={f.semantic_profile || uploads[f.kind].info?.semantic_profile} ledger={f.ledger_analysis} source={f.source} />)}<div className="source-profile-grid">{validation.files.filter((f) => !f.ledger_analysis).map((f) => <SourceUnderstanding key={f.kind} label={f.label} profile={f.semantic_profile || uploads[f.kind].info?.semantic_profile} source={f.source} />)}</div></section>
      {validation.files.some((f) => f.row_exceptions?.length) && <section className="workspace-section" data-testid="row-exceptions"><h3>Row exceptions</h3><table><thead><tr><th>Source row</th><th>Field</th><th>Value</th><th>Problem</th><th>Action</th></tr></thead><tbody>{validation.files.flatMap((f) => (f.row_exceptions || []).map((e) => <tr key={`${f.kind}-${e.source_row}-${e.field}`}><td>{e.source_row}</td><td>{e.field}</td><td>{e.value || "blank"}</td><td>{e.problem}</td><td>{e.action}</td></tr>))}</tbody></table></section>}
      {validation.files.some((f) => f.issues.filter((i) => i.severity !== "row_exception").length) && <div className="issue-list" data-testid="validation-issues"><h3>Issues</h3>{validation.files.flatMap((f) => f.issues.filter((i) => i.severity !== "row_exception").map((i, idx) => <article key={`${f.kind}-${idx}`} className={`issue ${i.severity}`} data-testid={`issue-${f.kind}-${idx}`}>{i.severity === "error" ? <XCircle size={16} /> : <AlertTriangle size={16} />}<div><b>{i.problem}</b><div className="issue-meta"><span>File: {i.file}</span><span>Field: {i.field}</span>{i.row_count > 0 && <span>Rows: {i.rows.join(", ")}{i.row_count > i.rows.length ? ` â€¦ (+${i.row_count - i.rows.length})` : ""}</span>}</div><p>{i.explanation}</p><p className="fix"><b>Recommended fix:</b> {i.recommended_fix}</p></div></article>))}</div>}
      <div className="wizard-actions"><button className="secondary-button" data-testid="back-to-upload-button" onClick={() => setStep(1)}><ArrowLeft size={14} /> Back to uploads</button><button className="primary-button" data-testid="start-reconciliation-button" disabled={!validation.can_process || starting || globalProcessing} onClick={start}>{starting ? <><Loader2 size={15} className="spin" /> Startingâ€¦</> : <>Start reconciliation <ArrowRight size={15} /></>}</button></div>
    </section>}

    {step === 3 && displayedJob && <ProcessingPanel job={displayedJob} onRetry={() => { setJob(null); setStep(2); }} onOpen={() => { selectRun(displayedJob.run_id || displayedJob.job_id); navigate("/reconciliation"); }} onNew={() => { setJob(null); setStep(1); setValidation(null); setUploads(emptyUploads()); }} />}

    {step === 1 && <section className="source-notes" data-testid="source-notes"><h2>Before you begin</h2><div><Check size={15} /> Assessee PAN identifies the client; deductor TAN identifies who deducted. TAN is never converted into PAN.</div><div><Check size={15} /> Customer Master TAN mappings are trusted identity signals; saved CA confirmations are reused on every run.</div><div><Check size={15} /> Review-required identities are never treated as exact matches â€” they wait for you in Identity Review.</div><div><Check size={15} /> Column headers are matched flexibly (e.g. "Party Name", "TDS Amount"). Download a template above to see the canonical schema.</div></section>}
  </>;
}

function UploadCard({ kind, title, description, icon: Icon, required, state, onPick, onRemove, disabled }: { kind: FileKind; title: string; description: string; icon: React.ElementType; required: boolean; state: UploadState; onPick: (f: File | null) => void; onRemove: () => void; disabled: boolean }) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const accept = kind === "form26as" ? PDF_ACCEPT : ACCEPT;
  const onDrop = (e: React.DragEvent) => { e.preventDefault(); setDrag(false); onPick(e.dataTransfer.files?.[0] || null); };
  return <article className={`upload-card ${state.status}`} data-testid={`${kind}-upload-card`}>
    <div className="upload-top"><div className="upload-icon"><Icon size={19} /></div><span className="file-type">{required ? "REQUIRED" : "OPTIONAL"} Â· CSV Â· XLSX Â· XLS{kind === "form26as" ? " Â· PDF" : ""}</span></div><h3>{title}</h3><p>{description}</p>
    {state.status === "idle" && <label className={`drop-zone ${drag ? "drag" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}><Upload size={16} /><span>Drop file here or <b>browse</b></span><input ref={inputRef} data-testid={`${kind}-file-input`} type="file" accept={accept} disabled={disabled} aria-label={`Upload ${title} file`} onChange={(e) => { onPick(e.target.files?.[0] || null); e.target.value = ""; }} /></label>}
    {state.status === "uploading" && <div className="file-progress" data-testid={`${kind}-upload-progress`}><Loader2 size={15} className="spin" /><div><span>{state.file?.name}</span><div className="progress-track"><i style={{ width: `${state.progress}%` }} /></div></div><b>{state.progress}%</b></div>}
    {state.status === "processing" && state.info && <div className="file-progress" data-testid={`${kind}-source-processing`}><Loader2 size={15} className="spin" /><div><span>{state.info.filename}</span><small>Upload stored · {state.info.source_processing_stage?.replaceAll("_", " ").toLowerCase() || "analysing source columns and rows"}{state.info.source_processing_total_rows ? ` · ${state.info.source_processing_processed_rows || 0} / ${state.info.source_processing_total_rows} rows` : ""}. You can keep this page open or refresh.</small></div></div>}
    {state.status === "done" && state.info && <div className={`file-selected ${state.info.diagnostic ? "needs-attention" : ""}`} data-testid={`${kind}-file-selected`}><CheckCircle2 size={17} /><span title={state.info.filename}>{state.info.filename}<small>{fileSize(state.info.size)} Â· {state.info.row_count} rows Â· {state.info.columns_mapped.length} columns recognised{state.info.columns_unmapped.length ? ` Â· ${state.info.columns_unmapped.length} ignored` : ""}</small>{state.info.diagnostic && <small className="file-diagnostic" data-testid={`${kind}-upload-diagnostic`}>{state.info.diagnostic}</small>}</span><label className="icon-button" title="Replace file" aria-label={`Replace ${title} file`} data-testid={`${kind}-replace-file-button`}><Replace size={15} /><input type="file" accept={accept} hidden onChange={(e) => { onPick(e.target.files?.[0] || null); e.target.value = ""; }} /></label><button className="icon-button" data-testid={`${kind}-remove-file-button`} aria-label={`Remove ${title} file`} onClick={onRemove}><X size={15} /></button></div>}
    {state.status === "error" && <div className="file-error" data-testid={`${kind}-upload-error`}><XCircle size={16} /><span>{state.file?.name || state.info?.filename}<small>{state.error}</small></span><label className="text-button" data-testid={`${kind}-retry-upload-button`}>Replace<input type="file" accept={accept} hidden onChange={(e) => { onPick(e.target.files?.[0] || null); e.target.value = ""; }} /></label><button className="icon-button" aria-label="Clear" onClick={onRemove}><X size={14} /></button></div>}
  </article>;
}

function ValidationCard({ file }: { file: FileValidation }) {
  const accountingSummary = file.row_summary?.tds_receivable_rows != null;
  return <article className={`validation-card ${file.status.toLowerCase()}`} data-testid={`validation-card-${file.kind}`}><header><div><b>{file.label}</b><small>{file.filename} Â· {file.row_count} rows</small></div><span className={`badge ${file.status === "VALID" ? "green" : file.status === "BLOCKED" ? "red" : "amber"}`}>{file.status}</span></header><ul>{file.checks.map((c) => <li key={c.code} data-testid={`check-${file.kind}-${c.code}`}><CheckIcon ok={c.passed} /><span>{c.label}</span><small>{c.detail}</small></li>)}</ul>{file.row_summary && <p className="unmapped">{accountingSummary ? <>Source rows: {file.row_summary.transaction_rows} · TDS Receivable: {file.row_summary.tds_receivable_rows} · Debit movements: {file.row_summary.tds_receivable_debit_rows} · Credit adjustments: {file.row_summary.tds_receivable_credit_rows} · Accounting review: {file.row_summary.accounting_role_review_rows} · Non-TDS excluded: {file.row_summary.excluded_non_transaction_rows}</> : <>Transaction rows: {file.row_summary.transaction_rows} · Valid: {file.row_summary.valid_rows} · Requires review: {file.row_summary.exception_rows} · Excluded non-transaction: {file.row_summary.excluded_non_transaction_rows}</>}</p>}{file.header_mappings?.length ? <p className="unmapped" data-testid={`header-mappings-${file.kind}`}>Mapped: {file.header_mappings.map((m) => `${m.source_header} → ${m.canonical_field}`).join(" · ")}</p> : null}{file.columns_unmapped.length > 0 && <p className="unmapped">Unmapped columns: {file.columns_unmapped.join(", ")}</p>}</article>;
}

const STEP_ICON: Record<StepState["status"], React.ElementType> = { PENDING: CircleDashed, RUNNING: Loader2, COMPLETED: CheckCircle2, FAILED: XCircle };
export function ProcessingPanel({ job, onRetry, onOpen, onNew }: { job: Job; onRetry: () => void; onOpen: () => void; onNew?: () => void }) {
  const steps = Array.isArray(job.steps) ? job.steps : [];
  return <section className="workspace-section processing-panel" data-testid="processing-panel">
    <div className="section-heading"><div><span className="eyebrow">PROCESSING</span><h2>{job.status === "PROCESSING" ? "Reconciliation in progress" : job.status === "COMPLETED" ? "Reconciliation complete" : "Reconciliation failed"}</h2></div><span className={`badge ${job.status === "COMPLETED" ? "green" : job.status === "FAILED" ? "red" : "amber"}`} data-testid="job-status">{job.status}</span></div>
    <div className="progress-track large"><i style={{ width: `${job.progress}%` }} /></div>
    <ol className="job-steps">{steps.map((s) => { const Icon = STEP_ICON[s.status]; return <li key={s.key} className={s.status.toLowerCase()} data-testid={`job-step-${s.key}`}><Icon size={17} className={s.status === "RUNNING" ? "spin" : ""} /><div><b>{s.label}</b>{s.detail && <small>{s.detail}</small>}</div></li>; })}</ol>
    {job.status === "FAILED" && <><ErrorNotice testId="job-error" message={job.error || "Processing failed."} onRetry={onRetry} />{job.validation?.some((f) => f.issues.length) && <div className="issue-list">{job.validation.flatMap((f) => f.issues.filter((i) => i.severity === "error").map((i, idx) => <article key={`${f.kind}-${idx}`} className="issue error"><XCircle size={16} /><div><b>{i.problem}</b><div className="issue-meta"><span>File: {i.file}</span><span>Field: {i.field}</span></div><p>{i.explanation}</p><p className="fix"><b>Recommended fix:</b> {i.recommended_fix}</p></div></article>))}</div>}</>}
    {job.status === "COMPLETED" && job.summary && <InfoNotice tone="success" testId="job-complete-notice"><b>{job.summary.result_count} result rows</b> Â· {job.summary.matched_claimable_count} matched & claimable Â· {job.summary.exceptions_count} exceptions Â· {job.summary.identity_review_count} identities to review</InfoNotice>}
    <div className="wizard-actions">{onNew && job.status !== "PROCESSING" && <button className="secondary-button" data-testid="start-another-button" onClick={onNew}><RefreshCw size={14} /> Start another</button>}<button className="primary-button" data-testid="open-results-button" disabled={job.status !== "COMPLETED"} onClick={onOpen}>Open reconciliation <ArrowRight size={15} /></button></div>
  </section>;
}

const readable = (value?: string | null) => value ? value.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, char => char.toUpperCase()) : "Not determined";
const money = (value?: number | null) => value == null ? "Not provided" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(value);

function SourceUnderstanding({ label, profile, ledger, source }: { label: string; profile?: SemanticProfile | null; ledger?: FileValidation["ledger_analysis"]; source?: FileValidation["source"] }) {
  if (!profile) return <article className="source-profile-card"><b>{label}</b><p className="muted">Source profile is unavailable for this upload.</p></article>;
  const fields = profile.columns.filter(column => column.status === "DETECTED");
  if (ledger) return <article className="ledger-source-summary" data-testid="tds-group-summary"><header><div><span className="eyebrow">TDS LEDGER</span><h4>{ledger.report_type === "TDS_RECEIVABLE_GROUP_SUMMARY" ? "TDS Receivable group summary" : label}</h4><p>{source?.sheet_name || "Source sheet not recorded"} · Accounting group-level report</p></div><span className="source-status review">Review-aware</span></header><div className="ledger-context"><div><span>Report period</span><b>{ledger.report_period || "Not provided"}</b></div><div><span>Financial year</span><b>{ledger.financial_year_context || "Not provided"}</b></div><div><span>Source granularity</span><b>Account group summary</b></div><div><span>Transaction dates</span><b>Not provided</b></div></div><div className="ledger-metrics"><div><span>Account groups</span><strong>{ledger.account_rows}</strong></div><div><span>Debit movements eligible</span><strong>{ledger.eligible_debit_rows}</strong></div><div><span>Credit-only review</span><strong>{ledger.credit_only_review_rows}</strong></div><div><span>Grand Total excluded</span><strong>{ledger.non_transaction_rows}</strong></div></div><div className="ledger-controls"><div><span>Debit control total</span><b>{money(ledger.control_totals.source_debit_total)}</b><small>Parsed: {money(ledger.control_totals.calculated_debit_total)}</small></div><div><span>Credit control total</span><b>{money(ledger.control_totals.source_credit_total)}</b><small>Parsed: {money(ledger.control_totals.calculated_credit_total)}</small></div><div className="control-match"><span>Control check</span><b>{ledger.control_totals.debit_difference === 0 && ledger.control_totals.credit_difference === 0 ? "Matched" : "Review required"}</b><small>Grand Total retained as a control only</small></div></div><div className="source-field-tags">{fields.map(column => <span key={column.source_column}><b>{column.source_column}</b><i>{readable(column.semantic_field)}</i></span>)}</div><p className="ledger-note">Only explicit TDS Receivable debit movements enter reconciliation. Credit-only movements remain visible for accounting review and are never converted into positive TDS.</p></article>;
  return <article className="source-profile-card"><header><div><span className="eyebrow">SOURCE PROFILE</span><h4>{label}</h4></div><span className="source-status">{fields.length} fields mapped</span></header><div className="source-field-tags">{fields.map(column => <span key={column.source_column}><b>{column.source_column}</b><i>{readable(column.semantic_field)}</i></span>)}</div>{profile.warnings.map(warning => <p className="source-warning" key={warning.code}><b>{readable(warning.code)}</b> {warning.message}</p>)}</article>;
}
