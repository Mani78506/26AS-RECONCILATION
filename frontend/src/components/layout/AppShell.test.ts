jest.mock("react-router-dom", () => ({ useLocation: jest.fn(), useNavigate: jest.fn() }), { virtual: true });

import { RECONCILIATION_NAV, TDS_COMPLIANCE_NAV, WORKSPACE_NAV } from "./AppShell";

describe("module-specific workspace navigation", () => {
  test("keeps reconciliation and TDS Compliance navigation separate", () => {
    expect(RECONCILIATION_NAV.map((item) => item.label)).toEqual(["Dashboard", "New Reconciliation", "Reconciliation", "Exceptions", "Identity Review", "Reports"]);
    expect(WORKSPACE_NAV.map((item) => item.label)).toEqual(["Choose Workspace"]);
    expect(TDS_COMPLIANCE_NAV.map((item) => item.label)).toEqual(["Compliance Overview", "Assignments", "Payment Ledger", "TDS Calculation", "Deposit & Challans", "Interest & Delay", "Return Audit", "Review Queue", "Reports"]);
    expect(TDS_COMPLIANCE_NAV.map((item) => item.label)).not.toContain("Reconciliation");
    expect(RECONCILIATION_NAV.map((item) => item.label)).not.toContain("TDS Compliance");
    expect(WORKSPACE_NAV.map((item) => item.label)).not.toContain("New Reconciliation");
  });
});
