import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import NewReconciliationPage from "./NewReconciliationPage";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const mockUpload = jest.fn();
const mockValidate = jest.fn();
const mockSelectRun = jest.fn();
const mockSearchParams = new URLSearchParams();
const mockWorkspace = { watchJob: jest.fn(), selectRun: mockSelectRun, clientId: null as string | null, run: null as { run_id: string; status: string } | null, processing: false };

jest.mock("../context/WorkspaceContext", () => ({
  useWorkspace: () => mockWorkspace,
}));
jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [mockSearchParams, jest.fn()] }), { virtual: true });
const mockQuery = jest.fn();
jest.mock("@tanstack/react-query", () => ({ useQuery: (...args: unknown[]) => mockQuery(...args) }));
jest.mock("sonner", () => ({ toast: { warning: jest.fn(), error: jest.fn() } }));
jest.mock("../services/api", () => ({
  api: { upload: (...args: unknown[]) => mockUpload(...args), validate: (...args: unknown[]) => mockValidate(...args), reconcile: jest.fn(), deleteUpload: jest.fn(), templateUrl: jest.fn() },
  getErrorMessage: () => "Backend unavailable",
}));

describe("New Reconciliation user flow", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    mockUpload.mockReset();
    mockValidate.mockReset();
    mockSelectRun.mockReset();
    mockWorkspace.run = null;
    mockWorkspace.clientId = null;
    mockSearchParams.delete("new_client");
    mockQuery.mockReturnValue({ data: null });
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  async function render() {
    await act(async () => { root.render(<NewReconciliationPage />); });
  }
  async function choose(label: string) {
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>('[role="radio"]')).find((item) => item.textContent?.includes(label));
    expect(button).toBeTruthy();
    await act(async () => { button!.click(); });
  }

  test("switches upload cards without retaining legacy workflow slots", async () => {
    await render();
    expect(host.querySelector('[data-testid="books-upload-card"]')).toBeTruthy();
    await choose("26AS-Only");
    expect(host.querySelector('[data-testid="books-upload-card"]')).toBeNull();
    expect(host.querySelector('[data-testid="form26as-upload-card"]')).toBeTruthy();
    await choose("Sales + TDS + 26AS");
    expect(host.querySelector('[data-testid="tds_receivable-upload-card"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="sales_registry-upload-card"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="customer_master-upload-card"]')).toBeNull();
    expect(host.textContent).toContain("Customer Master is not required");
  });

  test("exposes exactly the three reconciliation workflows", async () => {
    await render();
    expect(host.querySelectorAll('[role="radio"]')).toHaveLength(3);
    expect(host.textContent).not.toContain("TDS Compliance");
    expect(host.querySelector('[data-testid="tds-compliance-workflow"]')).toBeNull();
  });

  test("keeps the completed run selected while preparing another reconciliation", async () => {
    mockWorkspace.run = { run_id: "completed-run", status: "COMPLETED" };
    await render();
    expect(mockSelectRun).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="workflow-choice"]')).toBeTruthy();
    expect(host.querySelector('[data-testid="validate-files-button"]')).toBeTruthy();
  });

  test("starts a blank new-client draft without clearing the completed workspace", async () => {
    mockWorkspace.clientId = "existing-client";
    mockWorkspace.run = { run_id: "completed-run", status: "COMPLETED" };
    mockSearchParams.set("new_client", "1");
    mockQuery.mockReturnValue({ data: [{ client_id: "existing-client", client_name: "Existing Client", assessee_pan: "ABCDE1234F" }] });
    await render();
    expect((host.querySelector<HTMLInputElement>('[data-testid="assessee-name-input"]')!).value).toBe("");
    expect((host.querySelector<HTMLInputElement>('[data-testid="assessee-pan-input"]')!).value).toBe("");
    expect(mockSelectRun).not.toHaveBeenCalled();
  });

  test("Sales validation enables only after its three upload responses exist", async () => {
    mockUpload.mockImplementation((kind: string) => Promise.resolve({ upload_id: `${kind}-id`, kind, filename: `${kind}.csv`, size: 20, ext: ".csv", uploaded_at: "now", row_count: 1, columns_mapped: ["id"], columns_unmapped: [], readable: true }));
    await render();
    await choose("Sales + TDS + 26AS");
    const inputs = ["tds_receivable", "form26as", "sales_registry"].map((kind) => host.querySelector<HTMLInputElement>(`[data-testid="${kind}-file-input"]`)!);
    for (const input of inputs) {
      const file = new File(["value"], "source.csv", { type: "text/csv" });
      Object.defineProperty(input, "files", { configurable: true, value: [file] });
      await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    }
    const validateButton = host.querySelector<HTMLButtonElement>('[data-testid="validate-files-button"]')!;
    expect(validateButton.disabled).toBe(false);
  });
});
