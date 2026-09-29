jest.mock("react-router-dom", () => ({ Navigate: () => null, useNavigate: () => jest.fn(), useParams: () => ({}) }), { virtual: true });

import { resolveAssignmentPlan } from "./TdsAssignmentPlanPage";
import type { TdsComplianceWorkspace } from "../types";

const workspace = (overrides: Record<string, unknown> = {}) => ({
  assignment: { assignment_id: "a-1", workflow: "TDS_COMPLIANCE", organization_id: "org", client_id: "client", assessee_legal_name: "Acme Ltd", financial_year: "2025-26", status: "DRAFT", created_at: "2026-01-01", updated_at: "2026-01-01", version: 1 },
  overview: { workflow_stage: "SETUP", overall_review_state: "OPEN", last_updated: "2026-01-01", lock_status: "OPEN", transactions_uploaded: 0, transactions_classified: 0, transactions_calculated: 0, exceptions: 0, deposit_issues: 0, interest_issues: 0, reconciliation_issues: null, return_issues: null },
  stages: [], sources: [], exceptions: {}, runs: {},
  ...overrides,
} as unknown as TdsComplianceWorkspace);

const emptyData = { returnArtifacts: 0, returnExceptions: 0, transactionReviews: 0, interestRuns: 0 };

describe("assignment plan resolver", () => {
  test("guides an empty assignment to payment-ledger intake", () => {
    const plan = resolveAssignmentPlan(workspace(), emptyData);
    expect(plan.current.key).toBe("ledger");
    expect(plan.steps.find(step => step.key === "ledger")?.state).toBe("WAITING_FOR_INPUT");
  });

  test("keeps ledger validation/commit as a CA-controlled next action", () => {
    const plan = resolveAssignmentPlan(workspace({ sources: [{ key: "payment_ledger", label: "Payment ledger", uploaded: true, validated: true, committed: false, version: null, row_count: 20, validation_issues: 0, detail: null }] }), emptyData);
    expect(plan.steps.find(step => step.key === "ledger")?.state).toBe("WAITING_FOR_INPUT");
    expect(plan.steps.find(step => step.key === "calculation")?.state).toBe("BLOCKED");
  });

  test("exposes downstream steps from persisted records and never makes lock automatic", () => {
    const plan = resolveAssignmentPlan(workspace({
      overview: { workflow_stage: "RETURN_AUDIT", overall_review_state: "OPEN", last_updated: "2026-01-01", lock_status: "OPEN", transactions_uploaded: 12, transactions_classified: 12, transactions_calculated: 12, exceptions: 0, deposit_issues: 0, interest_issues: 0, reconciliation_issues: null, return_issues: null },
      sources: [{ key: "payment_ledger", label: "Payment ledger", uploaded: true, validated: true, committed: true, version: 1, row_count: 12, validation_issues: 0, detail: null }, { key: "deposit_evidence", label: "Deposit evidence", uploaded: true, validated: true, committed: true, version: 1, row_count: 12, validation_issues: 0, detail: null }],
      runs: { ledger_version_id: "ledger-1", calculation_id: "calc-1", deposit_run_id: "deposit-1", interest_run_id: "interest-1" },
    }), { returnArtifacts: 1, returnExceptions: 0, transactionReviews: 0, interestRuns: 1 });
    expect(plan.readyToLock).toBe(true);
    expect(plan.current.key).toBe("lock");
    expect(plan.steps.find(step => step.key === "lock")?.state).toBe("READY_FOR_REVIEW");
  });

  test("keeps final review blocked while persisted exceptions remain", () => {
    const plan = resolveAssignmentPlan(workspace({ overview: { workflow_stage: "REVIEW", overall_review_state: "OPEN", last_updated: "2026-01-01", lock_status: "OPEN", transactions_uploaded: 1, transactions_classified: 1, transactions_calculated: 1, exceptions: 1, deposit_issues: 1, interest_issues: 0, reconciliation_issues: null, return_issues: null }, runs: { ledger_version_id: "ledger-1", calculation_id: "calc-1", deposit_run_id: "deposit-1", interest_run_id: "interest-1" }, sources: [{ key: "payment_ledger", label: "Payment ledger", uploaded: true, validated: true, committed: true, version: 1, row_count: 1, validation_issues: 0, detail: null }, { key: "deposit_evidence", label: "Deposit evidence", uploaded: true, validated: true, committed: true, version: 1, row_count: 1, validation_issues: 0, detail: null }] }), { returnArtifacts: 1, returnExceptions: 1, transactionReviews: 1, interestRuns: 1 });
    expect(plan.readyToLock).toBe(false);
    expect(plan.openReviews).toBeGreaterThan(0);
    expect(plan.steps.find(step => step.key === "lock")?.state).toBe("BLOCKED");
  });
});
