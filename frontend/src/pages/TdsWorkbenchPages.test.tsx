import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TdsCalculationWorkbenchPage, TdsInterestPage, TdsLedgerPage } from "./TdsWorkbenchPages";
import { api } from "../services/api";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [new URLSearchParams("assignment=E2E_GOLDEN_PATH"), jest.fn()] }), { virtual: true });
jest.mock("@tanstack/react-query", () => {
  const assignment = { assignment_id: "E2E_GOLDEN_PATH", workflow: "TDS_COMPLIANCE", organization_id: "e2e-org", client_id: "e2e-client", assessee_legal_name: "E2E Demo TDS Services", financial_year: "2026-27", status: "DRAFT" };
  const transactionResults = { calculation: { calculation_id: "TDCC-FROZEN", ledger_version_id: "TDCLV-E2E" }, summary: { total_transactions: 1 }, items: [{ transaction_id: "E2E-G-001", transaction_status: "MATCHED", payment_date: "2026-04-10", deductee_name: "E2E Contractor", deductee_pan: "ABCDE1234F", amount: 50000, payment_nature: "contractor", payment_nature_source: "CLASSIFICATION_REVIEW", section_reference: "393(1) [Table: Sl. No. 6(i)]", table_reference: "Table: Sl. No. 6(i)", deductee_type: "INDIVIDUAL_HUF", recipient_residency: "RESIDENT", recipient_category: "INDIVIDUAL_HUF_CONTRACTOR", payer_category: "DESIGNATED_PERSON", governing_act: "Income-tax Act, 2025", rule_id: "STAT-393-6I-INDHUF-2026-V1", rule_version: "2026-27.official.v1", calculation_status: "CALCULATED", applicable_rate: 1, expected_tds: 500, tds_deducted: 500, tds_deduction_difference: 0 }] };
  const classification = { transaction_id: "E2E-HR-001", payment_date: "2026-04-10", deductee_name: "E2E Reviewer", amount: 50000, payment_nature: "contractor", payment_nature_source: "CA_REVIEW", payment_nature_confidence: "HIGH", payment_nature_status: "REVIEW_REQUIRED", classification_reason: "Source review", recipient_residency: "RESIDENT", recipient_category: "PERSON" };
  const history = [{ interest_run_id: "TDCI-POLICY-GATED", calculation_id: "TDCC-FROZEN", deposit_run_id: "TDCD-FROZEN" }];
  const interestDetail = { interest_run: history[0], items: [{ transaction_id: "E2E-G-001", overall_status: "POLICY_NOT_CONFIGURED", deduction_status: "DATE_NOT_DETERMINABLE", deposit_status: "DATE_NOT_DETERMINABLE", deposit_due_date: null, total_interest: null }] };
  return { useQuery: (options: { queryKey: string[] }) => {
    switch (options.queryKey[0]) {
      case "tds-compliance-assignments": return { data: [assignment], isLoading: false, isError: false };
      case "tds-calculations": return { data: [{ calculation_id: "TDCC-FROZEN", ledger_version_id: "TDCLV-E2E", created_at: "2026-10-05" }], isLoading: false, isError: false };
      case "tds-ledger": return { data: { ledger_version: { version: 1, ledger_version_id: "TDCLV-E2E" }, items: [] }, isLoading: false, isError: false };
      case "tds-classification-review": return { data: [classification], isLoading: false, isError: false };
      case "tds-interest-compliance": return { data: history, isLoading: false, isError: false };
      case "tds-interest-compliance-detail": return { data: interestDetail, isLoading: false, isError: false };
      default: return { data: transactionResults, isLoading: false, isError: false };
    }
  }, useMutation: (options: { mutationFn: (value?: unknown) => Promise<unknown>; onSuccess?: () => void }) => ({ mutate: (value?: unknown) => { options.mutationFn(value); }, isPending: false, isError: false, error: null }), useQueryClient: () => ({ invalidateQueries: jest.fn() }) };
});
jest.mock("../services/api", () => ({ api: { tdsComplianceAssignments: jest.fn(), tdsComplianceTransactions: jest.fn(), tdsPaymentLedger: jest.fn(), tdsClassificationReview: jest.fn(), classifyTdsTransaction: jest.fn().mockResolvedValue({ transaction_id: "E2E-HR-001", payment_nature: "contractor" }), runTdsCalculation: jest.fn().mockResolvedValue({ calculation: { calculation_id: "TDCC-FROZEN" }, items: [] }), tdsCalculations: jest.fn(), persistedInterestCompliance: jest.fn(), persistedInterestComplianceDetail: jest.fn() }, getErrorMessage: () => "Unavailable" }));

function renderPage(Page: () => React.ReactElement) {
  const host = document.createElement("div"); document.body.appendChild(host); const root = createRoot(host);
  return { host, root, render: async () => { await act(async () => { root.render(<Page />); }); }, dispose: () => { act(() => root.unmount()); host.remove(); } };
}

describe("TDS Calculation workbench", () => {
  test("shows frozen classification and selected-rule context for the persisted transaction", async () => {
    const page = renderPage(TdsCalculationWorkbenchPage); await page.render();
    const row = page.host.querySelector("tbody tr") as HTMLTableRowElement;
    await act(async () => { row.click(); });
    expect(page.host.textContent).toContain("E2E-G-001"); expect(page.host.textContent).toContain("Calculation status"); expect(page.host.textContent).toContain("INDIVIDUAL_HUF"); expect(page.host.textContent).toContain("RESIDENT"); expect(page.host.textContent).toContain("INDIVIDUAL_HUF_CONTRACTOR"); expect(page.host.textContent).toContain("DESIGNATED_PERSON"); expect(page.host.textContent).toContain("STAT-393-6I-INDHUF-2026-V1"); expect(page.host.textContent).toContain("2026-27.official.v1"); expect(page.host.textContent).toContain("CALCULATED");
    page.dispose();
  });
});

describe("TDS Interest workbench", () => {
  test("shows the persisted policy-gated interest result without inventing a value", async () => {
    const page = renderPage(TdsInterestPage); await page.render();
    expect(page.host.textContent).toContain("TDCI-POLICY-GATED");
    expect(page.host.textContent).toContain("POLICY_NOT_CONFIGURED");
    expect(page.host.textContent).toContain("No interest value was invented.");
    page.dispose();
  });
});


describe("TDS classification review", () => {
  test("offers horse-race nature as controlled source classification without exposing a statutory rule selector", async () => {
    const page = renderPage(TdsLedgerPage); await page.render();
    const rows = page.host.querySelectorAll("tbody tr");
    await act(async () => { (rows[rows.length - 1] as HTMLTableRowElement).click(); });
    const options = Array.from(page.host.querySelectorAll("select option")).map(option => option.textContent);
    expect(options).toContain("horse race");
    expect(options).toContain("benefit perquisite");
    expect(options).toContain("lottery commission");
    expect(options).toContain("national savings scheme");
    expect(options).toContain("partner remuneration interest");
    expect(options).toContain("purchase of goods");
    expect(options).toContain("ecommerce");
    expect(options).toContain("virtual digital asset");
    expect(options).toContain("ECOMMERCE OPERATOR");
    expect(options).not.toContain("393(3) Table 3");
    page.dispose();
  });

  test("submits only editable classification fields, never ledger display context", async () => {
    const page = renderPage(TdsLedgerPage); await page.render();
    const row = page.host.querySelector("tbody tr") as HTMLTableRowElement;
    await act(async () => { row.click(); });
    await act(async () => { (Array.from(page.host.querySelectorAll("button")).find(button => button.textContent === "Save classification") as HTMLButtonElement).click(); await Promise.resolve(); });
    const [, , payload] = (api.classifyTdsTransaction as any).mock.calls.at(-1);
    expect(payload).toMatchObject({ payment_nature: "contractor", recipient_residency: "RESIDENT", recipient_category: "PERSON", reason: "Source review" });
    expect(payload).not.toHaveProperty("payment_date");
    expect(payload).not.toHaveProperty("deductee_name");
    expect(payload).not.toHaveProperty("amount");
    expect(payload).not.toHaveProperty("payment_nature_source");
    expect(payload).not.toHaveProperty("payment_nature_confidence");
    page.dispose();
  });
});
