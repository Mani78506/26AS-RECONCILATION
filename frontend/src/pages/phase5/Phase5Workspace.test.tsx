import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import Phase5Workspace from './Phase5Workspace';
import { DetailDrawer, EvidenceTable, ResultsTable, SummaryCards } from './components';
import type { TdsComplianceAssignment } from '../../types';
import type { Context, DepositResult, DepositRun, Evidence, EvidenceVersion, Policy, Summary } from './types';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const mockApi = { versions: jest.fn(), evidence: jest.fn(), policies: jest.fn(), policy: jest.fn(), createPolicy: jest.fn(), history: jest.fn(), calculations: jest.fn(), interests: jest.fn(), calculation: jest.fn(), interest: jest.fn(), detail: jest.fn(), summary: jest.fn(), upload: jest.fn(), validate: jest.fn(), commit: jest.fn(), preview: jest.fn(), run: jest.fn() };
jest.mock('./api', () => ({ get phase5Api() { return mockApi; } }));
jest.mock('axios', () => ({ isAxiosError: (e: { isAxiosError?: boolean }) => !!e?.isAxiosError }));

const assignment: TdsComplianceAssignment = { assignment_id: 'A', client_id: 'CA', organization_id: 'ORG', assessee_legal_name: 'Acme Ltd', assessee_pan: 'ABCDE1234F', financial_year: '2026-27', workflow: 'TDS_COMPLIANCE', status: 'DRAFT', created_at: '', updated_at: '', version: 1 };
const policy: Policy = { assignment_id: 'A', client_id: 'CA', policy_id: 'P1', policy_version: '1', financial_years: ['2026-27'], effective_from: '2026-04-01', effective_to: '2027-03-31', priority: 1, active: true, status: 'APPROVED', authoritative_identifier_types: ['EXACT_TRANSACTION_REFERENCE'], permitted_relationship_types: ['ONE_TO_ONE', 'ONE_TO_MANY'], allocation_policy: 'EXACT_REFERENCE_ONLY', ambiguity_policy: 'REVIEW_REQUIRED' };
const evidence: Evidence = { evidence_id: 'E1', deposit_transaction_id: 'TX1', tds_amount: 1000, deposit_date: '2026-05-07', source_reference: 'REF1', validation_status: 'VALID', source_row_number: 1 };
const version: EvidenceVersion = { evidence_version_id: 'V1', version: 1, assignment_id: 'A', client_id: 'CA', source_file_id: 'UP1', source_file_name: 'evidence.csv', committed_at: '2026-05-08', validation_summary: { total_rows: 1, invalid_rows: 0, review_rows: 0 } };
const result: DepositResult = { transaction_id: 'TX1', expected_tds: 1000, actual_tds_deducted: 1000, deposited_tds: 1000, deposit_difference: 0, deposit_status: 'DEPOSIT_MATCHED', timeliness_status: 'DEPOSIT_ON_TIME', interest_status: 'COMPLIANT', overall_status: 'FULLY_DEPOSIT_COMPLIANT', evidence_entries: [evidence], phase5_rule_snapshot: policy, relationship_status: 'ESTABLISHED', relationship_id: 'REL1', liability_ids: ['TX1'], evidence_version_id: 'V1', calculation_id: 'C1', match_method: 'EXACT_TRANSACTION_REFERENCE' };
const summary: Summary = { liability_count: 1, evidence_count: 1, matched_count: 1, reconciled_count: 1, compliant_count: 1, review_required_count: 0, exception_count: 0, not_determinable_count: 0, ambiguous_count: 0, unmatched_count: 0, invalid_date_sequence_count: 0, expected_tds_total: 1000, actual_tds_deducted_total: 1000, deposited_tds_total: 1000 };
const run: DepositRun = { deposit_run_id: 'RUN1', assignment_id: 'A', client_id: 'CA', calculation_id: 'C1', interest_run_id: 'I1', evidence_version_id: 'V1', phase5_rule_snapshot: policy, created_at: '2026-05-08' };
const context: Context = { liabilities: [{ transaction_id: 'TX1', deductee_name: 'Vendor Ltd', deductee_pan: 'AAAAA1234A', financial_year: '2026-27', compliance_status: 'COMPLIANT', deduction_status: 'COMPLIANT', reason: '' }], timing: [{ transaction_id: 'TX1', actual_deduction_date: '2026-04-01', deposit_due_date: '2026-05-07', overall_status: 'COMPLIANT', explanation: '' }] };
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  Object.values(mockApi).forEach(fn => fn.mockReset());
  mockApi.policies.mockResolvedValue([policy]); mockApi.versions.mockResolvedValue([version]); mockApi.evidence.mockResolvedValue({ evidence_version: version, items: [evidence] }); mockApi.history.mockResolvedValue([]);
  mockApi.calculations.mockResolvedValue([{ calculation_id: 'C1', created_at: '2026-05-01' }]); mockApi.interests.mockResolvedValue([{ interest_run_id: 'I1', calculation_id: 'C1', created_at: '2026-05-02' }]);
  mockApi.calculation.mockResolvedValue(context.liabilities); mockApi.interest.mockResolvedValue(context.timing);
  mockApi.preview.mockResolvedValue({ items: [result], summary }); mockApi.run.mockResolvedValue({ deposit_run: run, items: [result], summary });
  mockApi.detail.mockResolvedValue({ deposit_run: run, items: [result] }); mockApi.summary.mockResolvedValue(summary); mockApi.policy.mockResolvedValue(policy);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
async function render(element = <Phase5Workspace assignment={assignment} />) { await act(async () => { root.render(element); }); }
async function click(text: string) { const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === text); expect(button).toBeTruthy(); await act(async () => { button!.click(); }); }
async function change(select: string, value: string) { const el = document.querySelector<HTMLSelectElement>(select)!; await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); }); }

test('overview renders persisted summary and historical read-only run', async () => {
  mockApi.history.mockResolvedValue([run]); await render();
  expect(host.textContent).toContain('TDS Deposit / Challan Compliance'); expect(host.textContent).toContain('Acme Ltd'); expect(host.textContent).toContain('Historical Run · Read-only');
  expect(host.querySelector('[data-testid="stat-total-liabilities"]')?.textContent).toContain('1'); expect(mockApi.summary).toHaveBeenCalledWith('A', 'RUN1');
  expect(host.textContent).toContain('₹1,000'); expect(host.textContent).toContain('P1 · V1');
});
test('missing evidence and absent summary never fabricate zero money', async () => {
  mockApi.versions.mockResolvedValue([]); await render(); expect(host.textContent).toContain('No deposit evidence committed'); expect(host.textContent).not.toContain('₹0');
  await render(<SummaryCards summary={{ ...summary, expected_tds_total: null, deposited_tds_total: null }} />); expect(host.textContent).not.toContain('₹0'); expect(host.textContent).toContain('Not Determinable');
});
test('missing policy shows Configuration Required and disables final run', async () => {
  mockApi.policies.mockResolvedValue([]); await render(); expect(host.textContent).toContain('Configuration Required');
  const button = Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Run Compliance'); expect(button?.disabled).toBe(true);
});
test('ambiguous policy preview displays review without silently selecting a policy', async () => {
  mockApi.preview.mockResolvedValue({ items: [{ ...result, phase5_rule_snapshot: null, reason_code: 'PHASE5_POLICY_AMBIGUOUS', overall_status: 'REVIEW_REQUIRED' }], summary: { ...summary, review_required_count: 1 } });
  await render(); await click('Preview Compliance'); expect(host.textContent).toContain('More than one policy is applicable. Resolve the policy selection before creating a saved run.'); expect(host.textContent).toContain('Review Required');
  expect(Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Run Compliance')?.disabled).toBe(true);
});
test('evidence table preserves missing source fields as Not Provided', async () => {
  await render(<EvidenceTable rows={[{ evidence_id: 'E1', validation_status: 'REVIEW_REQUIRED' }]} version={version} />);
  expect(host.textContent).toContain('Not Provided'); expect(host.textContent).toContain('V1'); expect(host.textContent).not.toContain('₹0');
});
test('result statuses use human readable labels and context', async () => {
  await render(<ResultsTable rows={[result]} context={context} onDetail={() => {}} />);
  expect(host.textContent).toContain('Compliant'); expect(host.textContent).toContain('On Time'); expect(host.textContent).toContain('Authoritative Mapping'); expect(host.textContent).toContain('Vendor Ltd'); expect(host.textContent).not.toContain('FULLY_DEPOSIT_COMPLIANT');
});
test('detail shows every linked evidence row and immutable provenance', async () => {
  await render(<DetailDrawer row={{ ...result, evidence_entries: [evidence, { ...evidence, evidence_id: 'E2', challan_number: 'CH2' }] }} context={context} run={run} version={version} onClose={() => {}} />);
  const dialog = document.querySelector('[role="dialog"]')!; expect(dialog.textContent).toContain('Evidence E1'); expect(dialog.textContent).toContain('Evidence E2'); expect(dialog.textContent).toContain('CH2'); expect(dialog.textContent).toContain('RUN1'); expect(dialog.textContent).toContain('P1 · V1');
});
test('many liabilities to one evidence displays shared relationship with no invented allocation', async () => {
  await render(<DetailDrawer row={{ ...result, liability_ids: ['TX1', 'TX2'] }} context={context} onClose={() => {}} />);
  const dialog = document.querySelector('[role="dialog"]')!; expect(dialog.textContent).toContain('Shared evidence across liabilities: TX1, TX2'); expect(dialog.querySelectorAll('.phase5-evidence-card').length).toBe(1); expect(dialog.textContent).toContain('Not Provided');
});
test('invalid date sequence has explicit manual review explanation', async () => {
  await render(<DetailDrawer row={{ ...result, timeliness_status: 'INVALID_DATE_SEQUENCE', overall_status: 'REVIEW_REQUIRED' }} context={context} onClose={() => {}} />);
  expect(document.body.textContent).toContain('Deposit date precedes deduction date — Manual Review Required'); expect(document.body.textContent).toContain('Invalid Date Sequence');
});
test('preview uses exact selected persisted inputs and renders counts without creating a run', async () => {
  await render(); await click('Preview Compliance'); expect(mockApi.preview).toHaveBeenCalledWith('A', { calculation_id: 'C1', interest_run_id: 'I1', evidence_version_id: 'V1' });
  expect(host.textContent).toContain('Preview ready. No historical run has been created.'); expect(host.querySelector('[data-testid="phase5-summary"]')).toBeTruthy(); expect(mockApi.run).not.toHaveBeenCalled();
});
test('run requires confirmation then displays new run with persisted summary', async () => {
  await render(); await click('Preview Compliance'); await click('Run Compliance'); expect(mockApi.run).not.toHaveBeenCalled(); expect(document.body.textContent).toContain('Confirm New Compliance Run');
  await click('Confirm & Create Run'); expect(mockApi.run).toHaveBeenCalledTimes(1); expect(mockApi.summary).toHaveBeenCalledWith('A', 'RUN1'); expect(host.textContent).toContain('RUN1'); expect(host.textContent).toContain('Historical Run · Read-only');
});
test('API errors and access denial are controlled and do not reveal stack traces', async () => {
  mockApi.versions.mockRejectedValue({ isAxiosError: true, response: { status: 403 }, stack: 'secret stack' }); await render(); expect(host.textContent).toContain('Access denied.'); expect(host.textContent).not.toContain('secret stack');
});
test('network error supports retry', async () => {
  mockApi.versions.mockRejectedValueOnce(new Error('secret')).mockResolvedValue([version]); await render(); expect(host.textContent).toContain('Unable to load deposit compliance data. Please try again.'); await click('Retry'); expect(host.textContent).not.toContain('Unable to load deposit compliance data.');
});
test('upload must be validated before explicit commit and creates a separate version', async () => {
  mockApi.upload.mockResolvedValue({ upload_id: 'UP2', filename: 'new.csv' }); mockApi.validate.mockResolvedValue({ file: { filename: 'new.csv' }, rows: [evidence], mapped_headers: ['tds_amount'], unmapped_headers: [], mapping_warnings: [], summary: version.validation_summary, can_commit: true });
  mockApi.commit.mockResolvedValue({ ...version, evidence_version_id: 'V2', version: 2, source_file_name: 'new.csv' }); await render();
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!; Object.defineProperty(input, 'files', { configurable: true, value: [new File(['data'], 'new.csv', { type: 'text/csv' })] });
  await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(mockApi.validate).not.toHaveBeenCalled(); expect(Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Commit Evidence')?.disabled).toBe(true);
  await click('Validate Evidence'); expect(mockApi.commit).not.toHaveBeenCalled(); await click('Commit Evidence'); expect(mockApi.commit).toHaveBeenCalledWith('A', 'UP2'); expect(host.textContent).toContain('Evidence committed · V2');
});
test('input changes invalidate an existing preview and prevent stale run creation', async () => {
  await render(); await click('Preview Compliance'); await change('select', ''); expect(Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Run Compliance')?.disabled).toBe(true);
});
test('ambiguous mapping shows candidate IDs and no confirm mapping button', async () => {
  await render(<DetailDrawer row={{ ...result, overall_status: 'REVIEW_REQUIRED', candidate_evidence_ids: ['E8', 'E9'], reason_code: 'AMBIGUOUS_DEPOSIT_MAPPING' }} context={context} onClose={() => {}} />);
  expect(document.body.textContent).toContain('Candidate evidence: E8, E9'); expect(document.body.textContent).toContain('A controlled review action is not available'); expect(document.body.textContent).not.toContain('Confirm Mapping');
});
test('search and status filters restrict result rows', async () => {
  await render(<ResultsTable rows={[result, { ...result, transaction_id: 'TX2', overall_status: 'DEPOSIT_AMOUNT_EXCEPTION' }]} context={context} onDetail={() => {}} />);
  await change('select[aria-label="Filter overall"]', 'DEPOSIT_AMOUNT_EXCEPTION'); expect(host.querySelector('tbody')?.textContent).toContain('TX2'); expect(host.querySelector('tbody')?.textContent).not.toContain('TX1');
});
test('historical run opens its original calculation and interest context', async () => {
  mockApi.history.mockResolvedValue([{ ...run, deposit_run_id: 'OLD' }]); mockApi.detail.mockResolvedValue({ deposit_run: { ...run, deposit_run_id: 'OLD', calculation_id: 'C0', interest_run_id: 'I0' }, items: [result] });
  await render(); expect(mockApi.calculation).toHaveBeenCalledWith('A', 'C0'); expect(mockApi.interest).toHaveBeenCalledWith('A', 'I0'); expect(host.textContent).toContain('OLD');
});
test('unresolved policy and unknown totals stay unknown in preview', async () => {
  mockApi.preview.mockResolvedValue({ items: [{ ...result, phase5_rule_snapshot: null, reason_code: 'PHASE5_POLICY_NOT_FOUND', deposited_tds: null, deposit_difference: null }], summary: { ...summary, expected_tds_total: null, deposited_tds_total: null } });
  await render(); await click('Preview Compliance'); expect(host.querySelector('[data-testid="phase5-summary"]')?.textContent).not.toContain('₹0'); expect(host.textContent).toContain('Phase 5 policy configuration required');
});
