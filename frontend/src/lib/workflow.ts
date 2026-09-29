import type { FileKind, SourceSet, Workflow } from "../types";

export const WORKFLOW_UPLOAD_KINDS: Record<Workflow, FileKind[]> = {
  FULL_RECONCILIATION: ["books", "form26as", "customer_master"],
  "26AS_ONLY": ["form26as", "customer_master"],
  SALES_TDS_26AS: ["tds_receivable", "form26as", "sales_registry"],
};

export type UploadIds = Partial<Record<FileKind, string | null | undefined>>;

/** Keep source payloads and readiness checks isolated by workflow. */
export function workflowSourceIds(workflow: Workflow, uploads: UploadIds): SourceSet {
  if (workflow === "SALES_TDS_26AS") return {
    tds_receivable_upload_id: uploads.tds_receivable || null,
    form26as_upload_id: uploads.form26as || null,
    sales_registry_upload_id: uploads.sales_registry || null,
  };
  if (workflow === "26AS_ONLY") return {
    form26as_upload_id: uploads.form26as || null,
    customer_master_upload_id: uploads.customer_master || null,
  };
  return {
    books_upload_id: uploads.books || null,
    form26as_upload_id: uploads.form26as || null,
    customer_master_upload_id: uploads.customer_master || null,
  };
}

export function workflowUploadsReady(workflow: Workflow, uploads: UploadIds): boolean {
  const source = workflowSourceIds(workflow, uploads);
  return workflow === "SALES_TDS_26AS"
    ? Boolean(source.tds_receivable_upload_id && source.form26as_upload_id && source.sales_registry_upload_id)
    : workflow === "26AS_ONLY"
      ? Boolean(source.form26as_upload_id)
      : Boolean(source.books_upload_id && source.form26as_upload_id);
}

export function workflowRequirement(workflow: Workflow): string {
  return workflow === "SALES_TDS_26AS"
    ? "TDS Expected / Receivable, 26AS and Sales Registry are required to validate"
    : workflow === "26AS_ONLY"
      ? "26AS is required to validate"
      : "Books and 26AS are required to validate";
}
