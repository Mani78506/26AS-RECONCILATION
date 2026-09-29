import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import TdsCompliancePage from "./TdsCompliancePage";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

jest.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: [{ assignment_id: "TDCA-1", workflow: "TDS_COMPLIANCE", organization_id: "org", client_id: "client", assessee_legal_name: "Acme Ltd", financial_year: "2026-27", status: "DRAFT" }], isLoading: false, isError: false, refetch: jest.fn() }) }));
jest.mock("../services/api", () => ({ api: { tdsComplianceAssignments: jest.fn(), createTdsComplianceAssignment: jest.fn(), upload: jest.fn(), validateTdsPaymentLedger: jest.fn(), commitTdsPaymentLedger: jest.fn() }, getErrorMessage: () => "Unavailable" }));
jest.mock("../context/WorkspaceContext", () => ({ useWorkspace: () => ({ run: null, clientId: "client-test" }) }));

describe("TDS Compliance assignments page", () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  test("keeps the register separate from compliance workspaces", async () => {
    await act(async () => { root.render(<TdsCompliancePage />); });
    expect(host.textContent).toContain("Assignments");
    expect(host.textContent).toContain("Create draft assignment");
    expect(host.textContent).toContain("Payment Ledger, TDS Calculation, Deposit & Challans");
    expect(host.textContent).toContain("Current assignments");
    expect(host.textContent).toContain("Open review queue");
    expect(host.textContent).not.toContain("Government Tax Credit Summary");
    expect(host.textContent).not.toContain("Deposit Compliance");
    expect(host.querySelector('input[placeholder="2026-27"]')).toBeTruthy();
  });
});
