import { AlertCircle, AlertTriangle, Check, CheckCircle2, ChevronLeft, ChevronRight, FolderOpen, Info, Loader2, RefreshCw, X } from "lucide-react";
import { CATEGORY_LABELS, IDENTITY_LABELS, METHOD_LABELS, RESULT_LABELS, STATUS_LABELS, title } from "../../lib/format";

export function PageHeader({ eyebrow = "WORKSPACE", title: heading, subtitle, action }: { eyebrow?: string; title: string; subtitle?: string; action?: React.ReactNode }) {
  return <div className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1 data-testid="page-title">{heading}</h1>{subtitle && <p>{subtitle}</p>}</div>{action && <div className="page-actions">{action}</div>}</div>;
}

export function EmptyState({ title: heading, text, action, icon: Icon = FolderOpen, testId = "empty-state" }: { title: string; text: string; action?: React.ReactNode; icon?: React.ElementType; testId?: string }) {
  return <div className="empty-state" data-testid={testId}><div className="empty-icon"><Icon size={22} /></div><h3>{heading}</h3><p>{text}</p>{action}</div>;
}

export function ErrorNotice({ message, onRetry, onBack, testId = "api-error-message" }: { message: string; onRetry?: () => void; onBack?: () => void; testId?: string }) {
  return <div className="api-notice" role="alert" data-testid={testId}><AlertCircle size={16} /><span>{message}</span><div className="notice-actions">{onBack && <button className="text-button" data-testid="error-back-button" onClick={onBack}>Back</button>}{onRetry && <button className="text-button" data-testid="api-retry-button" onClick={onRetry}><RefreshCw size={13} /> Retry</button>}</div></div>;
}

export function InfoNotice({ children, tone = "info", testId }: { children: React.ReactNode; tone?: "info" | "warning" | "success"; testId?: string }) {
  const Icon = tone === "warning" ? AlertTriangle : tone === "success" ? CheckCircle2 : Info;
  return <div className={`info-notice ${tone}`} data-testid={testId}><Icon size={15} /><div>{children}</div></div>;
}

export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return <span className="spinner" role="status" aria-live="polite"><Loader2 size={size} className="spin" />{label && <span>{label}</span>}</span>;
}

export function Skeleton({ h = 14, w = "100%", className = "" }: { h?: number; w?: number | string; className?: string }) {
  return <span className={`skeleton ${className}`} style={{ height: h, width: w }} aria-hidden="true" />;
}

export function TableSkeleton({ rows = 8, cols = 8 }: { rows?: number; cols?: number }) {
  return <div className="table-skeleton" data-testid="table-skeleton">{Array.from({ length: rows }).map((_, r) => <div key={r} className="skeleton-row">{Array.from({ length: cols }).map((__, c) => <Skeleton key={c} h={12} w={`${60 + ((r * 7 + c * 13) % 40)}%`} />)}</div>)}</div>;
}

export function StatCard({ label, value, detail, tone = "blue", icon: Icon, onClick, testId, loading }: { label: string; value: string; detail?: string; tone?: string; icon: React.ElementType; onClick?: () => void; testId?: string; loading?: boolean }) {
  const Tag: any = onClick ? "button" : "article";
  return <Tag className={`stat-card ${onClick ? "clickable" : ""}`} data-testid={testId || `stat-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`} onClick={onClick}><div className={`stat-icon ${tone}`}><Icon size={16} /></div><span>{label}</span>{loading ? <Skeleton h={22} w="60%" /> : <strong>{value}</strong>}{detail && <small>{detail}</small>}</Tag>;
}

const RESULT_TONE: Record<string, string> = { MATCHED_CLAIMABLE: "green", MATCHED_NOT_CLAIMABLE: "amber", AMOUNT_MISMATCH: "red", MISSING_IN_BOOKS: "amber", MISSING_IN_26AS: "red", IDENTITY_UNMAPPED: "purple", DUPLICATE_BOOK: "slate", DUPLICATE_26AS: "slate" };
export function ResultBadge({ result, compact }: { result: string; compact?: boolean }) { return <span className={`badge ${RESULT_TONE[result] || "slate"}`} data-testid={`result-badge-${result}`}>{compact ? (RESULT_LABELS[result] || title(result)).replace("Matched · ", "") : RESULT_LABELS[result] || title(result)}</span>; }
export function ClaimBadge({ value }: { value: string }) { return <span className={`badge ${value === "CLAIMABLE" ? "green" : value === "NOT_CLAIMABLE" ? "amber" : "muted"}`} data-testid={`claim-badge-${value}`}>{value === "NOT_APPLICABLE" ? "N/A" : title(value)}</span>; }
export function IdentityBadge({ value }: { value: string }) { return <span className={`badge ${value === "EXACT" ? "green" : value === "HIGH_CONFIDENCE" ? "blue" : value === "REVIEW_REQUIRED" ? "amber" : "red"}`} data-testid={`identity-badge-${value}`}>{IDENTITY_LABELS[value] || title(value)}</span>; }
export function MethodBadge({ value }: { value: string }) { return <span className={`badge outline ${value === "UNMATCHED" ? "muted" : ""}`}>{METHOD_LABELS[value] || title(value)}</span>; }
export function SeverityBadge({ value }: { value: string }) { return <span className={`badge severity ${value.toLowerCase()}`} data-testid={`severity-badge-${value}`}>{title(value)}</span>; }
export function StatusBadge({ value }: { value: string }) { return <span className={`badge status-${value.toLowerCase()}`} data-testid={`status-badge-${value}`}>{STATUS_LABELS[value] || title(value)}</span>; }
export function CategoryLabel({ value }: { value: string | null }) { return <>{value ? CATEGORY_LABELS[value] || title(value) : "—"}</>; }

export function Amount({ value, signed, strong }: { value: number | null | undefined; signed?: boolean; strong?: boolean }) {
  if (value === null || value === undefined) return <span className="amount muted">—</span>;
  const neg = value < 0, pos = value > 0;
  const text = `${neg ? "−" : signed && pos ? "+" : ""}₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(Math.abs(value))}`;
  return <span className={`amount ${signed ? (neg ? "negative" : pos ? "positive-diff" : "zero") : ""} ${strong ? "strong" : ""}`}>{text}</span>;
}

export function Pagination({ page, pageSize, total, onPage, onPageSize }: { page: number; pageSize: number; total: number; onPage: (p: number) => void; onPageSize?: (s: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return <div className="pagination" data-testid="pagination"><span data-testid="pagination-summary">{from}–{Math.min(total, page * pageSize)} of {total}</span>{onPageSize && <select aria-label="Rows per page" data-testid="page-size-select" value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}>{[25, 50, 100, 200].map((s) => <option key={s} value={s}>{s} / page</option>)}</select>}<button className="icon-button" data-testid="pagination-prev" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={16} /></button><b>{page} / {pages}</b><button className="icon-button" data-testid="pagination-next" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight size={16} /></button></div>;
}

export function Drawer({ open, onClose, title: heading, subtitle, children, width = 720, testId = "drawer" }: { open: boolean; onClose: () => void; title: React.ReactNode; subtitle?: React.ReactNode; children: React.ReactNode; width?: number; testId?: string }) {
  if (!open) return null;
  return <div className="drawer-root" data-testid={testId}><div className="drawer-backdrop" onClick={onClose} aria-hidden="true" /><aside className="drawer" role="dialog" aria-modal="true" aria-label={typeof heading === "string" ? heading : "Detail"} style={{ width }} onKeyDown={(e) => e.key === "Escape" && onClose()}><header className="drawer-head"><div><h2>{heading}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" data-testid="drawer-close-button" aria-label="Close" onClick={onClose} autoFocus><X size={18} /></button></header><div className="drawer-body">{children}</div></aside></div>;
}

export function Field({ label, children, mono, testId }: { label: string; children: React.ReactNode; mono?: boolean; testId?: string }) {
  return <div className="field" data-testid={testId}><span>{label}</span><b className={mono ? "mono" : ""}>{children ?? "—"}</b></div>;
}

export function CheckIcon({ ok }: { ok: boolean }) { return ok ? <Check size={14} className="ok" /> : <X size={14} className="bad" />; }

export function ConfirmDialog({ open, title: heading, description, confirmLabel, busy, danger, onCancel, onConfirm, testId = "confirm-dialog" }: { open: boolean; title: string; description: React.ReactNode; confirmLabel: string; busy?: boolean; danger?: boolean; onCancel: () => void; onConfirm: () => void; testId?: string }) {
  if (!open) return null;
  return <div className="modal-root" data-testid={testId} onKeyDown={(e) => e.key === "Escape" && onCancel()}><div className="drawer-backdrop" onClick={onCancel} aria-hidden="true" /><div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title"><h2 id="confirm-title">{heading}</h2><div className="modal-body">{description}</div><div className="modal-actions"><button className="secondary-button" data-testid={`${testId}-cancel`} onClick={onCancel} disabled={busy}>Cancel</button><button className={`primary-button ${danger ? "danger" : ""}`} data-testid={`${testId}-confirm`} onClick={onConfirm} disabled={busy} autoFocus>{busy ? "Saving…" : confirmLabel}</button></div></div></div>;
}
