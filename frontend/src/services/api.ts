import axios, { AxiosError } from "axios";
import type { AIChatResponse, AIContextInfo, Customer, DepositCompliancePreview, DepositComplianceReview, DepositEvidenceValidation, DepositEvidenceVersion, ExceptionState, FileKind, GovernmentEvidenceValidation, GovernmentEvidenceVerification, GovernmentEvidenceVerificationReview, GovernmentEvidenceVersion, IdentityReview, InterestCompliancePreview, Job, Paged, PaymentLedgerValidation, PersistedInterestCompliancePreview, ReconciliationOverview, ReconciliationResult, ReconciliationSummary, Run, SalesTds26asResult, SalesTdsException, Settings, SourceSet, TdsCalculationPreview, TdsCalculatorInput, TdsCalculatorRecord, TdsCalculatorResult, TdsComplianceAssignment, TdsComplianceRule, TdsComplianceRuleDraft, TdsComplianceWorkspace, TdsRule, TdsTransactionWorkspace, UploadInfo, ValidationResponse, WorkspaceClient } from "../types";

// Deployment supplies a complete backend origin.  With no origin configured,
// use the hosting origin so reverse-proxied production deployments work without
// embedding a local address or a transient tunnel in the frontend bundle.
const backendOrigin = process.env.REACT_APP_BACKEND_URL?.trim().replace(/\/$/, "") || "";
const baseURL = `${backendOrigin}/api`;
export const client = axios.create({ baseURL, timeout: 30000, headers: { Accept: "application/json" } });

export const getErrorMessage = (error: unknown, fallback = "Reconciliation service is unavailable. Check the backend connection and try again."): string => {
  const err = error as AxiosError<{ detail?: unknown }>;
  if (!err || !err.isAxiosError) return fallback;
  if (!err.response) return err.code === "ECONNABORTED" ? "The request timed out. The service may be busy â€” try again." : fallback;
  const detail = err.response.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d: any) => d.msg || String(d)).join("; ");
  if (err.response.status >= 500) return "The reconciliation service reported an internal error. Try again or check the backend logs.";
  return fallback;
};

const params = (obj: Record<string, unknown>) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== "ALL"));

export const api = {
  health: () => client.get("/health").then((r) => r.data),
  meta: () => client.get("/meta").then((r) => r.data),
  templateUrl: (kind: FileKind) => `${baseURL}/templates/${kind}`,
  schema: (kind: FileKind) => client.get(`/schema/${kind}`).then((r) => r.data),

  upload: (kind: FileKind, file: File, onProgress?: (p: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    return client.post<UploadInfo>("/uploads", form, { headers: { "Content-Type": "multipart/form-data" }, timeout: 120000, onUploadProgress: (e) => onProgress?.(e.total ? Math.round((e.loaded / e.total) * 100) : 100) }).then((r) => r.data);
  },
  uploadStatus: (id: string) => client.get<UploadInfo>(`/uploads/${encodeURIComponent(id)}`).then((r) => r.data),
  deleteUpload: (id: string) => client.delete(`/uploads/${id}`).then((r) => r.data),
  validate: (src: SourceSet) => client.post<ValidationResponse>("/validate", src).then((r) => r.data),
  reconcile: (src: SourceSet) => client.post<Job>("/reconcile", src).then((r) => r.data),
  startBooksValidation: (id: string, src: SourceSet) => client.post(`/books-validation/${encodeURIComponent(id)}`, src).then((r) => r.data),
  booksValidation: (id: string) => client.get(`/books-validation/${encodeURIComponent(id)}`).then((r) => r.data),
  loadSample: () => client.post<Job & { uploads: Record<FileKind, string> }>("/sample/load").then((r) => r.data),
  job: (id: string) => client.get<Job>(`/jobs/${id}`).then((r) => r.data),
  rerun: (runId: string) => client.post<Job>(`/runs/${runId}/rerun`).then((r) => r.data),
  aiContext: (runId: string) => client.get<{ run_id: string; context: AIContextInfo }>(`/ai/context/${encodeURIComponent(runId)}`).then((r) => r.data),
  aiChat: (body: { run_id: string; question: string; result_id?: string; conversation_id?: string; filters?: Record<string, string> }) => client.post<AIChatResponse>("/ai/chat", body, { timeout: 120000 }).then((r) => r.data),

  runs: () => client.get<Run[]>("/runs").then((r) => r.data),
  clients: () => client.get<{ clients: WorkspaceClient[] }>("/clients").then((r) => r.data.clients),
  clientRuns: (clientId: string) => client.get<{ client: Pick<WorkspaceClient, "client_id" | "client_name" | "assessee_pan">; runs: Run[] }>(`/clients/${encodeURIComponent(clientId)}/runs`).then((r) => r.data),
  latestRun: () => client.get<Run | Record<string, never>>("/runs/latest").then((r) => (r.data && "run_id" in r.data ? (r.data as Run) : null)),
  run: (id: string, clientId?: string | null) => client.get<Run>(`/runs/${id}`, { params: params({ client_id: clientId }) }).then((r) => r.data),

  summary: (runId?: string | null) => client.get<ReconciliationOverview>("/reconciliation/summary", { params: params({ run_id: runId }) }).then((r) => r.data),
  results: (query: Record<string, unknown>) => client.get<Paged<ReconciliationResult>>("/reconciliation/results", { params: params(query) }).then((r) => r.data),
  result: (id: string, runId?: string | null) => client.get<ReconciliationResult>(`/reconciliation/results/${encodeURIComponent(id)}`, { params: params({ run_id: runId }) }).then((r) => r.data),
  salesTdsSummary: (runId: string) => client.get<{ run: Run; summary: Record<string, number | string | Record<string, number>> }>("/sales-tds-26as/summary", { params: { run_id: runId } }).then((r) => r.data),
  salesTdsResults: (runId: string) => client.get<Paged<SalesTds26asResult>>("/sales-tds-26as/results", { params: { run_id: runId, page_size: 500 } }).then((r) => r.data),
  salesTdsExceptions: (runId: string) => client.get<Paged<SalesTdsException>>("/sales-tds-26as/exceptions", { params: { run_id: runId, page_size: 500 } }).then((r) => r.data),

  exceptions: (query: Record<string, unknown>) => client.get<Paged<ReconciliationResult>>("/exceptions", { params: params(query) }).then((r) => r.data),
  updateException: (id: string, body: { status?: string; note?: string; assignee?: string; reviewer?: string }) => client.patch<ExceptionState>(`/exceptions/${encodeURIComponent(id)}`, body).then((r) => r.data),

  identityReviews: (runId?: string | null, status?: string) => client.get<{ items: IdentityReview[]; run_id: string | null; counts: Record<string, number>; assessee_pan?: string }>("/identity/reviews", { params: params({ run_id: runId, status }) }).then((r) => r.data),
  identityConfirm: (body: { run_id: string; tan: string; customer_code?: string; reviewer: string; note?: string }) => client.post<{ message: string; job_id: string | null }>("/identity/confirm", body).then((r) => r.data),
  identityReject: (body: { run_id: string; tan: string; customer_code?: string; reviewer: string; note?: string }) => client.post<{ message: string; job_id: string | null }>("/identity/reject", body).then((r) => r.data),
  identityKeepUnmapped: (body: { run_id: string; tan: string; reviewer: string; note?: string }) => client.post<{ message: string; job_id: string | null }>("/identity/keep-unmapped", body).then((r) => r.data),
  identityAlias: (body: { run_id: string; alias: string; customer_code: string; reviewer: string; rerun?: boolean }) => client.post<{ message: string; job_id: string | null }>("/identity/alias", body).then((r) => r.data),
  identityMappings: (pan?: string) => client.get("/identity/mappings", { params: params({ assessee_pan: pan }) }).then((r) => r.data),
  customers: (runId: string, q?: string) => client.get<{ items: Customer[] }>("/customers", { params: params({ run_id: runId, q }) }).then((r) => r.data.items),

  reportsAvailable: (runId?: string | null) => client.get<{ run_id: string | null; reports: { key: string; label: string; available: boolean }[]; formats: string[] }>("/reports/available", { params: params({ run_id: runId }) }).then((r) => r.data),
  reportHistory: (runId?: string | null) => client.get<{ run_id: string; report: string; format: string; rows: number; generated_at: string; filename: string }[]>("/reports/history", { params: params({ run_id: runId }) }).then((r) => r.data),
  downloadReport: async (report: string, format: string, runId?: string | null) => {
    const res = await client.get("/reports/export", { params: params({ report, format, run_id: runId }), responseType: "blob", timeout: 120000 });
    const disposition = res.headers["content-disposition"] || "";
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = match?.[1] || `${report}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return a.download;
  },

  settings: () => client.get<{ settings: Settings; defaults: Settings; editable: boolean }>("/settings").then((r) => r.data),
  updateSettings: (body: Partial<Settings>) => client.put<{ settings: Settings; defaults: Settings }>("/settings", body).then((r) => r.data),
  tdsRules: (activeOnly = false) => client.get<{ rules: TdsRule[] }>("/tds-rules", { params: { active_only: activeOnly } }).then((r) => r.data.rules),
  createTdsRule: (body: TdsRule) => client.post<TdsRule>("/tds-rules", body).then((r) => r.data),
  updateTdsRule: (id: string, body: TdsRule) => client.put<TdsRule>(`/tds-rules/${encodeURIComponent(id)}`, body).then((r) => r.data),
  deactivateTdsRule: (id: string) => client.post<{ rule_id: string; is_active: boolean }>(`/tds-rules/${encodeURIComponent(id)}/deactivate`).then((r) => r.data),
  tdsComplianceAssignments: () => client.get<{ items: TdsComplianceAssignment[] }>("/tds-compliance/assignments").then((r) => r.data.items),
  tdsComplianceWorkspace: (assignmentId: string) => client.get<TdsComplianceWorkspace>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/workspace`).then((r) => r.data),
  tdsComplianceTransactions: (assignmentId: string) => client.get<TdsTransactionWorkspace>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/transactions`).then((r) => r.data),
  reviewTdsComplianceTransaction: (assignmentId: string, transactionId: string, body: { action: "REVIEW" | "COMMENT" | "RESOLVE" | "REOPEN"; reason?: string; comment?: string }) => client.post(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/transactions/${encodeURIComponent(transactionId)}/review`, body).then((r) => r.data),
  tdsComplianceRules: () => client.get<{ rules: TdsComplianceRule[] }>("/tds-compliance/rules").then((r) => r.data.rules),
  tdsClassificationMappings: () => client.get<{ items: Record<string, unknown>[] }>("/tds-compliance/classification-mappings").then((r) => r.data.items),
  createTdsComplianceRule: (body: TdsComplianceRuleDraft) => client.post<TdsComplianceRule>("/tds-compliance/rules", body).then((r) => r.data),
  updateTdsComplianceRule: (id: string, body: TdsComplianceRuleDraft) => client.put<TdsComplianceRule>(`/tds-compliance/rules/${encodeURIComponent(id)}`, body).then((r) => r.data),
  submitTdsComplianceRule: (id: string) => client.post<TdsComplianceRule>(`/tds-compliance/rules/${encodeURIComponent(id)}/submit`).then((r) => r.data),
  approveTdsComplianceRule: (id: string) => client.post<TdsComplianceRule>(`/tds-compliance/rules/${encodeURIComponent(id)}/approve`).then((r) => r.data),
  activateTdsComplianceRule: (id: string) => client.post<TdsComplianceRule>(`/tds-compliance/rules/${encodeURIComponent(id)}/activate`).then((r) => r.data),
  deactivateTdsComplianceRule: (id: string) => client.post<TdsComplianceRule>(`/tds-compliance/rules/${encodeURIComponent(id)}/deactivate`).then((r) => r.data),
  tdsComplianceRuleHistory: (id: string) => client.get<{ items: unknown[] }>(`/tds-compliance/rules/${encodeURIComponent(id)}/history`).then((r) => r.data.items),
  tdsComplianceRuleAudit: (id: string) => client.get<{ items: unknown[] }>(`/tds-compliance/rules/${encodeURIComponent(id)}/audit`).then((r) => r.data.items),
  createTdsComplianceAssignment: (body: Omit<TdsComplianceAssignment, "assignment_id" | "workflow" | "created_at" | "updated_at" | "locked_at" | "version">) => client.post<TdsComplianceAssignment>("/tds-compliance/assignments", body).then((r) => r.data),
  lockTdsComplianceAssignment: (assignmentId: string) => client.post<TdsComplianceAssignment>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/lock`).then((r) => r.data),
  validateTdsPaymentLedger: (assignmentId: string, uploadId: string) => client.post<PaymentLedgerValidation>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/payment-ledger/validate`, { upload_id: uploadId }).then((r) => r.data),
  commitTdsPaymentLedger: (assignmentId: string, uploadId: string) => client.post(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/payment-ledger/commit`, { upload_id: uploadId }).then((r) => r.data),
  uploadReturnAuditArtifact: (assignmentId: string, file: File) => { const form = new FormData(); form.append("file", file); return client.post(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/upload`, form).then((r) => r.data); },
  returnAuditArtifacts: (assignmentId: string) => client.get<{ items: import("../types").ReturnAuditArtifact[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/artifacts`).then((r) => r.data.items),
  runReturnAudit: (assignmentId: string, artifactId: string, calculationId?: string) => client.post<{ audit_run: import("../types").ReturnAuditRun; items: import("../types").ReturnAuditResult[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/run`, { artifact_id: artifactId, calculation_id: calculationId || null }).then((r) => r.data),
  returnAuditSummary: (assignmentId: string, auditRunId?: string) => client.get<{ latest_run: import("../types").ReturnAuditRun | null }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/summary`, { params: { audit_run_id: auditRunId || undefined } }).then((r) => r.data.latest_run),
  returnAuditResults: (assignmentId: string) => client.get<{ items: import("../types").ReturnAuditResult[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/results`).then((r) => r.data.items),
  returnAuditReviewDecisions: (assignmentId: string, auditRunId?: string) => client.get<{ items: import("../types").ReturnAuditReviewDecision[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/review-decisions`, { params: { audit_run_id: auditRunId || undefined } }).then((r) => r.data.items),
  createReturnAuditReviewDecision: (assignmentId: string, resultId: string, decision: string, reason: string) => client.post<import("../types").ReturnAuditReviewDecision>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/return-audit/results/${encodeURIComponent(resultId)}/review-decisions`, { decision, reason }).then((r) => r.data),
  tdsCalculations: (assignmentId: string) => client.get<{ items: { calculation_id: string; created_at: string; ledger_version_id?: string }[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculations`).then((r) => r.data.items),
  uploadGovernmentEvidence: (assignmentId: string, file: File) => { const form = new FormData(); form.append("file", file); return client.post<UploadInfo>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/upload`, form, { headers: { "Content-Type": "multipart/form-data" }, timeout: 120000 }).then((r) => r.data); },
  validateGovernmentEvidence: (assignmentId: string, uploadId: string) => client.post<GovernmentEvidenceValidation>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/validate`, { upload_id: uploadId }).then((r) => r.data),
  commitGovernmentEvidence: (assignmentId: string, uploadId: string) => client.post<{ evidence_version: GovernmentEvidenceVersion }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/commit`, { upload_id: uploadId }).then((r) => r.data),
  governmentEvidence: (assignmentId: string) => client.get<{ items: GovernmentEvidenceVersion[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence`).then((r) => r.data.items),
  governmentEvidenceVersion: (assignmentId: string, evidenceVersionId: string) => client.get<{ evidence_version: GovernmentEvidenceVersion; items: import("../types").GovernmentEvidenceRow[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/${encodeURIComponent(evidenceVersionId)}`).then((r) => r.data),
  verifyGovernmentEvidence: (assignmentId: string, body: { evidence_version_id?: string; calculation_id?: string } = {}) => client.post<{ verification: GovernmentEvidenceVerification }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/verify`, body).then((r) => r.data),
  governmentEvidenceVerifications: (assignmentId: string) => client.get<{ items: GovernmentEvidenceVerification[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/verification`).then((r) => r.data.items),
  governmentEvidenceVerification: (assignmentId: string, verificationId: string) => client.get<{ verification: GovernmentEvidenceVerification; review: GovernmentEvidenceVerificationReview | null }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/verification/${encodeURIComponent(verificationId)}`).then((r) => r.data),
  reviewGovernmentEvidenceVerification: (assignmentId: string, verificationId: string, body: { action: "REVIEW" | "COMMENT" | "RESOLVE" | "REOPEN"; reason?: string; comment?: string }) => client.post<{ review: GovernmentEvidenceVerificationReview }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/government-evidence/verification/${encodeURIComponent(verificationId)}/review`, body).then((r) => r.data),
  tdsPaymentLedger: (assignmentId: string) => client.get<{ ledger_version: Record<string, unknown> | null; items: unknown[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/payment-ledger`).then((r) => r.data),
  tdsPaymentLedgerSummary: (assignmentId: string) => client.get(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/payment-ledger/summary`).then((r) => r.data),
  previewTdsCalculation: (assignmentId: string, ledgerVersionId?: string) => client.post<TdsCalculationPreview>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculations/preview`, { ledger_version_id: ledgerVersionId }).then((r) => r.data),
  runTdsCalculation: (assignmentId: string, ledgerVersionId?: string) => client.post<{ calculation: Record<string, unknown>; items: TdsCalculationPreview["items"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculations/run`, { ledger_version_id: ledgerVersionId }).then((r) => r.data),
  previewInterestCompliance: (assignmentId: string, calculationId: string) => client.post<InterestCompliancePreview>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest/preview`, { calculation_id: calculationId }).then((r) => r.data),
  runInterestCompliance: (assignmentId: string, calculationId: string) => client.post<{ interest_run: Record<string, unknown>; items: InterestCompliancePreview["items"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest/run`, { calculation_id: calculationId }).then((r) => r.data),
  previewPersistedInterestCompliance: (assignmentId: string, calculationId: string, depositRunId: string) => client.post<PersistedInterestCompliancePreview>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest-compliance/preview`, { calculation_id: calculationId, deposit_run_id: depositRunId }).then((r) => r.data),
  runPersistedInterestCompliance: (assignmentId: string, calculationId: string, depositRunId: string) => client.post<{ interest_run: Record<string, unknown>; items: PersistedInterestCompliancePreview["items"]; summary: PersistedInterestCompliancePreview["summary"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest-compliance/run`, { calculation_id: calculationId, deposit_run_id: depositRunId }).then((r) => r.data),
  persistedInterestCompliance: (assignmentId: string) => client.get<{ items: Record<string, unknown>[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest-compliance`).then((r) => r.data.items),
  persistedInterestComplianceDetail: (assignmentId: string, interestRunId: string) => client.get<{ interest_run: Record<string, unknown>; items: PersistedInterestCompliancePreview["items"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest-compliance/${encodeURIComponent(interestRunId)}`).then((r) => r.data),
  reviewPersistedInterestCompliance: (assignmentId: string, interestRunId: string, transactionId: string, body: { action: "REVIEW" | "COMMENT" | "RESOLVE" | "REOPEN"; reason?: string; comment?: string }) => client.post(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/interest-compliance/${encodeURIComponent(interestRunId)}/transactions/${encodeURIComponent(transactionId)}/review`, body).then((r) => r.data),
  validateDepositEvidence: (assignmentId: string, uploadId: string) => client.post<DepositEvidenceValidation>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-evidence/validate`, { upload_id: uploadId }).then((r) => r.data),
  uploadDepositEvidence: (assignmentId: string, file: File) => { const form = new FormData(); form.append("file", file); return client.post<UploadInfo>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-evidence/upload`, form, { headers: { "Content-Type": "multipart/form-data" }, timeout: 120000 }).then((r) => r.data); },
  commitDepositEvidence: (assignmentId: string, uploadId: string) => client.post<{ evidence_version: { evidence_version_id: string } }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-evidence/commit`, { upload_id: uploadId }).then((r) => r.data),
  depositEvidence: (assignmentId: string) => client.get<{ items: DepositEvidenceVersion[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-evidence`).then((r) => r.data.items),
  depositEvidenceVersion: (assignmentId: string, evidenceVersionId: string) => client.get<{ evidence_version: DepositEvidenceVersion; items: Record<string, unknown>[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-evidence/${encodeURIComponent(evidenceVersionId)}`).then((r) => r.data),
  previewDepositCompliance: (assignmentId: string, calculationId: string, evidenceVersionId?: string | null) => client.post<DepositCompliancePreview>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-compliance/preview`, { calculation_id: calculationId, evidence_version_id: evidenceVersionId }).then((r) => r.data),
  runDepositCompliance: (assignmentId: string, calculationId: string, evidenceVersionId?: string | null) => client.post<{ deposit_run: Record<string, unknown>; items: DepositCompliancePreview["items"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-compliance/run`, { calculation_id: calculationId, evidence_version_id: evidenceVersionId }).then((r) => r.data),
  depositComplianceHistory: (assignmentId: string) => client.get<{ items: Record<string, unknown>[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-compliance/history`).then((r) => r.data.items),
  depositComplianceDetail: (assignmentId: string, depositRunId: string) => client.get<{ deposit_run: Record<string, unknown>; items: DepositCompliancePreview["items"] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-compliance/${encodeURIComponent(depositRunId)}`).then((r) => r.data),
  reviewDepositCompliance: (assignmentId: string, depositRunId: string, transactionId: string, body: { action: "REVIEW" | "COMMENT" | "RESOLVE" | "REOPEN"; reason?: string; comment?: string }) => client.post<{ review: DepositComplianceReview }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/deposit-compliance/${encodeURIComponent(depositRunId)}/transactions/${encodeURIComponent(transactionId)}/review`, body).then((r) => r.data),
  previewTdsCalculator: (assignmentId: string, body: TdsCalculatorInput) => client.post<TdsCalculatorResult>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculator/preview`, body).then((r) => r.data),
  previewStandaloneTdsCalculator: (body: TdsCalculatorInput) => client.post<TdsCalculatorResult>("/tds-compliance/calculator/preview", body).then((r) => r.data),
  tdsCalculatorOptions: (financialYear: string) => client.get<{ items: { payment_nature: string; provision?: string | null; governing_act?: string | null }[] }>(`/tds-compliance/calculator/payment-natures?financial_year=${encodeURIComponent(financialYear)}`).then((r) => r.data.items),
  saveTdsCalculator: (assignmentId: string, body: TdsCalculatorInput) => client.post<TdsCalculatorRecord>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculator/calculate`, body).then((r) => r.data),
  tdsCalculatorHistory: (assignmentId: string) => client.get<{ items: TdsCalculatorRecord[] }>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculator/history`).then((r) => r.data.items),
  tdsCalculatorDetail: (assignmentId: string, id: string) => client.get<TdsCalculatorRecord>(`/tds-compliance/assignments/${encodeURIComponent(assignmentId)}/calculator/${encodeURIComponent(id)}`).then((r) => r.data),
};
