export const inr = (value: number | null | undefined, opts: { compact?: boolean } = {}): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const neg = value < 0;
  const abs = Math.abs(value);
  const formatted = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: Number.isInteger(abs) ? 0 : 2 }).format(abs);
  return `${neg ? "−" : ""}₹${formatted}`;
};

export const pct = (value: number | null | undefined, digits = 1): string => (value === null || value === undefined || Number.isNaN(value) ? "—" : `${Number(value.toFixed(digits))}%`);

export const num = (value: number | null | undefined): string => (value === null || value === undefined ? "—" : new Intl.NumberFormat("en-IN").format(value));

export const maskPan = (pan?: string | null): string => (!pan ? "—" : pan.length === 10 && localStorage.getItem("26as-mask-pan") !== "false" ? `${pan.slice(0, 5)}****${pan.slice(9)}` : pan);

export const fmtDate = (iso?: string | null): string => {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
};

export const fmtDateTime = (iso?: string | null): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const fileSize = (bytes: number): string => (bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

export const title = (value?: string | null): string => (value || "").replaceAll("_", " ").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());

export const RESULT_LABELS: Record<string, string> = { MATCHED_CLAIMABLE: "Matched", MATCHED_NOT_CLAIMABLE: "Matched – Not Claimable", AMOUNT_MISMATCH: "Amount Mismatch", MISSING_IN_BOOKS: "Missing in Books", MISSING_IN_26AS: "Missing in 26AS", IDENTITY_UNMAPPED: "Deductor Unmapped", DUPLICATE_BOOK: "Duplicate in Books", DUPLICATE_26AS: "Duplicate in 26AS" };
export const METHOD_LABELS: Record<string, string> = { EXACT_1_TO_1_SAME_QUARTER: "Exact 1:1 · Same Qtr", EXACT_1_TO_1_OUTSIDE_QUARTER: "Exact 1:1 · Outside Qtr", GROUP_SAME_QUARTER: "Group · Same Qtr", GROUP_OUTSIDE_QUARTER: "Group · Outside Qtr", UNMATCHED: "Unmatched" };
export const IDENTITY_LABELS: Record<string, string> = { EXACT: "Exact", HIGH_CONFIDENCE: "High Confidence", REVIEW_REQUIRED: "Review Required", UNMAPPED: "Unmapped" };
Object.assign(RESULT_LABELS, { CONSISTENT_WITH_CONFIGURED_RULE: "Consistent with Configured Rule", TDS_DIFFERENCE: "TDS Difference", NOT_DETERMINABLE: "Not Determinable" });
Object.assign(METHOD_LABELS, { ANALYSIS_ONLY: "26AS-Only Analysis" });
export const IDENTITY_METHOD_LABELS: Record<string, string> = { CUSTOMER_MASTER: "Customer Master (TAN)", SAVED_MAPPING: "Saved mapping (CA confirmed)", EXACT_NAME: "Exact legal name", MASTER_ALIAS: "Master alias", SAVED_ALIAS: "Saved alias", FUZZY: "Name similarity", NONE: "No signal" };
export const CATEGORY_LABELS: Record<string, string> = { ALL: "All", AMOUNT_MISMATCH: "Amount Mismatch", MISSING_IN_BOOKS: "Missing in Books", MISSING_IN_26AS: "Missing in 26AS", IDENTITY: "Identity", DUPLICATES: "Duplicates", NOT_CLAIMABLE: "Not Claimable" };
export const STATUS_LABELS: Record<string, string> = { OPEN: "Open", IN_REVIEW: "In Review", RESOLVED: "Resolved", IGNORED: "Ignored" };
export const FILE_LABELS: Record<string, string> = { books: "Books", form26as: "26AS / Form 16A", customer_master: "Customer Master" };
