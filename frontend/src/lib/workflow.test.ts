import { WORKFLOW_UPLOAD_KINDS, workflowRequirement, workflowSourceIds, workflowUploadsReady } from "./workflow";

const all = { books: "books", form26as: "statement", customer_master: "master", tds_receivable: "tds", sales_registry: "sales" };

describe("workflow source isolation", () => {
  test("exposes the correct upload cards for every workflow", () => {
    expect(WORKFLOW_UPLOAD_KINDS.FULL_RECONCILIATION).toEqual(["books", "form26as", "customer_master"]);
    expect(WORKFLOW_UPLOAD_KINDS["26AS_ONLY"]).toEqual(["form26as", "customer_master"]);
    expect(WORKFLOW_UPLOAD_KINDS.SALES_TDS_26AS).toEqual(["tds_receivable", "form26as", "sales_registry"]);
  });

  test("switching to Sales creates a three-source payload without stale Books or Customer Master", () => {
    const payload = workflowSourceIds("SALES_TDS_26AS", all);
    expect(payload).toEqual({ tds_receivable_upload_id: "tds", form26as_upload_id: "statement", sales_registry_upload_id: "sales" });
    expect(payload).not.toHaveProperty("books_upload_id");
    expect(payload).not.toHaveProperty("customer_master_upload_id");
  });

  test("Sales validation needs exactly TDS Expected/Receivable, 26AS and Sales Registry", () => {
    expect(workflowUploadsReady("SALES_TDS_26AS", { tds_receivable: "tds", form26as: "statement", sales_registry: "sales" })).toBe(true);
    expect(workflowUploadsReady("SALES_TDS_26AS", { tds_receivable: "tds", form26as: "statement", customer_master: "stale-master" })).toBe(false);
    expect(workflowRequirement("SALES_TDS_26AS")).toContain("Sales Registry");
  });

  test("Full and 26AS-only retain their existing required sources", () => {
    expect(workflowUploadsReady("FULL_RECONCILIATION", { books: "books", form26as: "statement" })).toBe(true);
    expect(workflowUploadsReady("FULL_RECONCILIATION", { form26as: "statement" })).toBe(false);
    expect(workflowUploadsReady("26AS_ONLY", { form26as: "statement" })).toBe(true);
  });
});
