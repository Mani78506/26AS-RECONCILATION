import { useEffect, useRef, useState } from 'react';
import { ClipboardCheck, RefreshCw, Upload } from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import { EmptyState, ErrorNotice, InfoNotice, PageHeader, Spinner } from '../../components/common';
import type { TdsComplianceAssignment } from '../../types';
import { phase5Api } from './api';
import { Badge, DetailDrawer, EvidenceTable, ResultsTable, SummaryCards } from './components';
import Policies from './Policies';
import { label, phase5Error, provided, warnings } from './format';
import type { CalculationRun, Context, DepositResult, DepositRun, Evidence, EvidenceValidation, EvidenceVersion, Inputs, InterestRun, Policy, PolicyInput, Preview, RunDetail, Summary } from './types';
import './phase5.css';

export default function Phase5Workspace({ assignment }: { assignment: TdsComplianceAssignment }) {
  const a = assignment.assignment_id;
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [success, setSuccess] = useState('');
  const [policies, setPolicies] = useState<Policy[]>([]), [versions, setVersions] = useState<EvidenceVersion[]>([]), [history, setHistory] = useState<DepositRun[]>([]);
  const [calculations, setCalculations] = useState<CalculationRun[]>([]), [interests, setInterests] = useState<InterestRun[]>([]);
  const [inputs, setInputs] = useState<Inputs>({ calculation_id: '', evidence_version_id: null, interest_run_id: null });
  const [evidence, setEvidence] = useState<Evidence[]>([]), [validation, setValidation] = useState<EvidenceValidation | null>(null), [uploadId, setUploadId] = useState(''), [filename, setFilename] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null), [detail, setDetail] = useState<RunDetail | null>(null), [summary, setSummary] = useState<Summary | null>(null), [context, setContext] = useState<Context>({ liabilities: [], timing: [] });
  const [row, setRow] = useState<DepositResult | null>(null), [policyDetail, setPolicyDetail] = useState<Policy | null>(null), [confirm, setConfirm] = useState(false);
  const mounted = useRef(true), fileInput = useRef<HTMLInputElement>(null), resultsRef = useRef<HTMLElement>(null);
  const operation = useRef(false);
  const locked = assignment.status === 'LOCKED';
  const disabled = loading || !!busy;
  const selectedVersion = versions.find(v => v.evidence_version_id === inputs.evidence_version_id);
  const displayedVersion = versions.find(v => v.evidence_version_id === (detail?.deposit_run.evidence_version_id || inputs.evidence_version_id));
  const rows = detail?.items || preview?.items || [];
  const selectedPolicies = Array.from(new Map((preview?.items || []).flatMap(r => r.phase5_rule_snapshot ? [[r.phase5_rule_snapshot.policy_id, r.phase5_rule_snapshot] as const] : [])).values());
  const policyAmbiguous = preview?.items.some(r => r.reason_code === 'PHASE5_POLICY_AMBIGUOUS');
  const policyMissing = preview?.items.some(r => r.reason_code === 'PHASE5_POLICY_NOT_FOUND') || !policies.length;
  const runReady = !!preview?.items.length && !policyAmbiguous && !policyMissing && !locked;

  async function perform(name: string, action: () => Promise<void>) {
    if (operation.current) return false;
    operation.current = true; setBusy(name); setError(''); setSuccess('');
    try { await action(); return true; } catch (e) { if (mounted.current) setError(phase5Error(e)); return false; }
    finally { operation.current = false; if (mounted.current) setBusy(''); }
  }
  async function getContext(body: Inputs): Promise<Context> {
    const [liabilities, timing] = await Promise.all([phase5Api.calculation(a, body.calculation_id), body.interest_run_id ? phase5Api.interest(a, body.interest_run_id) : Promise.resolve([])]);
    return { liabilities, timing };
  }
  async function fetchRun(runId: string) {
    const [d, s] = await Promise.all([phase5Api.detail(a, runId), phase5Api.summary(a, runId)]);
    const c = await getContext({ calculation_id: d.deposit_run.calculation_id, evidence_version_id: d.deposit_run.evidence_version_id || null, interest_run_id: d.deposit_run.interest_run_id || null });
    if (!mounted.current) return;
    setDetail(d); setSummary(s); setContext(c); setRow(null);
  }
  async function load() {
    setLoading(true); setError('');
    try {
      const [p, v, h, c, i] = await Promise.all([phase5Api.policies(a), phase5Api.versions(a), phase5Api.history(a), phase5Api.calculations(a), phase5Api.interests(a)]);
      const latest = v[0];
      const source = latest ? await phase5Api.evidence(a, latest.evidence_version_id) : null;
      if (!mounted.current) return;
      setPolicies(p); setVersions(v); setHistory(h); setCalculations(c); setInterests(i); setEvidence(source?.items || []);
      setInputs({ calculation_id: c[0]?.calculation_id || '', evidence_version_id: latest?.evidence_version_id || null, interest_run_id: i.find(r => r.calculation_id === c[0]?.calculation_id)?.interest_run_id || null });
      setPreview(null); setDetail(null); setSummary(null); setContext({ liabilities: [], timing: [] });
      if (h[0]) await fetchRun(h[0].deposit_run_id);
    } catch (e) { if (mounted.current) setError(phase5Error(e)); }
    finally { if (mounted.current) setLoading(false); }
  }
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; /* Assignment is keyed by the parent: never retain another scope's state. */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a]);
  function invalidate(next: Inputs) { setInputs(next); setPreview(null); setDetail(null); setSummary(null); setRow(null); setContext({ liabilities: [], timing: [] }); setSuccess(''); }
  async function chooseVersion(id: string) {
    await perform('Loading evidence', async () => { const result = id ? await phase5Api.evidence(a, id) : null; if (!mounted.current) return; setEvidence(result?.items || []); invalidate({ ...inputs, evidence_version_id: id || null }); });
  }
  async function upload(file?: File) {
    if (!file) return;
    setValidation(null); setUploadId(''); setFilename(file.name);
    await perform('Uploading evidence', async () => { const result = await phase5Api.upload(a, file); if (!mounted.current) return; setUploadId(result.upload_id); setFilename(result.filename); setSuccess('Evidence uploaded. Validate the source before committing.'); });
  }
  async function validate() { await perform('Validating evidence', async () => { const result = await phase5Api.validate(a, uploadId); if (mounted.current) { setValidation(result); setSuccess(result.can_commit ? 'Validation complete. Review the source rows, then commit explicitly.' : 'Validation found issues. Correct the source and upload again.'); } }); }
  async function commit() {
    await perform('Committing evidence', async () => { const v = await phase5Api.commit(a, uploadId); if (!mounted.current) return; setVersions(current => [v, ...current]); setEvidence(validation?.rows || []); setUploadId(''); setValidation(null); invalidate({ ...inputs, evidence_version_id: v.evidence_version_id }); setSuccess(`Evidence committed · V${v.version} · ${v.validation_summary.total_rows} rows · ${v.source_file_name}`); });
  }
  async function createPolicy(body: PolicyInput) {
    return perform('Saving policy', async () => { const p = await phase5Api.createPolicy(a, body); if (mounted.current) { setPolicies(current => [...current, p]); invalidate(inputs); setSuccess(`Policy saved · V${p.policy_version}. Preview to check applicability.`); } });
  }
  async function previewRun() {
    await perform('Preparing preview', async () => { const [p, c] = await Promise.all([phase5Api.preview(a, inputs), getContext(inputs)]); if (!mounted.current) return; setPreview(p); setDetail(null); setSummary(p.summary); setContext(c); setSuccess('Preview ready. No historical run has been created.'); });
  }
  async function run() {
    if (!runReady) return;
    await perform('Running compliance', async () => {
      const r = await phase5Api.run(a, inputs);
      if (!mounted.current) return;
      setConfirm(false); setPreview(null); setHistory(current => [r.deposit_run, ...current]);
      // Display the created ID even if a subsequent summary read fails; never automatically repeat a run.
      setDetail(r); setSummary(null); setSuccess(`Run created · ${r.deposit_run.deposit_run_id}. A new immutable snapshot was saved.`);
      const persisted = await phase5Api.summary(a, r.deposit_run.deposit_run_id);
      if (mounted.current) { setSummary(persisted); resultsRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }); }
    });
  }
  return <section id="phase5-deposit-compliance" className="phase5-workspace" aria-label="TDS deposit and challan compliance" aria-busy={disabled}>
    <PageHeader eyebrow="TDS COMPLIANCE · DEPOSIT & CHALLAN" title="TDS Deposit / Challan Compliance" subtitle={`${assignment.assessee_legal_name} · PAN ${provided(assignment.assessee_pan)} · FY ${assignment.financial_year}`} action={<button className="secondary-button" disabled={disabled} onClick={() => void load()}><RefreshCw size={14} /> Refresh</button>} />
    <div className="phase5-context"><span>Assignment: {a}</span><span>TAN: {provided(assignment.tan)}</span><span>{detail ? `Saved run: ${detail.deposit_run.deposit_run_id}` : 'New run workspace'}</span><Badge value={assignment.status} /></div>
    <ol className="phase5-steps">{['Source evidence', 'Validate file', 'Commit version', 'Confirm policy', 'Preview result', 'Create saved run', 'Review & audit'].map(s => <li key={s}>{s}</li>)}</ol>
    {error && <ErrorNotice message={error} onRetry={() => void load()} />}{success && <InfoNotice tone="success">{success}</InfoNotice>}{(loading || busy) && <Spinner label={busy || 'Loading deposit compliance workspace…'} />}
    {locked && <InfoNotice tone="warning">This assignment is locked. Evidence and saved results remain available for review.</InfoNotice>}
    <div className="phase5-mode"><InfoNotice tone={policyMissing || policyAmbiguous ? 'warning' : 'info'}><b>{policyAmbiguous ? 'CA review required' : policyMissing ? 'Policy configuration required' : selectedPolicies.length ? 'Ready for review' : 'Policy configured — preview required'}</b> · {policyAmbiguous ? 'More than one policy is applicable. Resolve the policy selection before creating a saved run.' : policyMissing ? 'A deposit-matching policy must be configured before this compliance review can be run.' : selectedPolicies.length ? selectedPolicies.map(p => `Selected policy: ${p.policy_id} · Version ${p.policy_version}`).join('; ') : 'The preview determines whether the selected policy applies to these transactions.'}</InfoNotice></div>
    <section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">COMPLIANCE REVIEW INPUTS</span><h3>Select the evidence for this review</h3><p className="muted">Choose the frozen calculation, timing record and deposit-evidence version to be assessed together.</p></div><ClipboardCheck size={20} /></div><div className="form-grid">
      <label>TDS calculation snapshot<select disabled={disabled} value={inputs.calculation_id} onChange={e => invalidate({ ...inputs, calculation_id: e.target.value, interest_run_id: interests.find(i => i.calculation_id === e.target.value)?.interest_run_id || null })}><option value="">Select a completed calculation</option>{calculations.map(c => <option key={c.calculation_id} value={c.calculation_id}>{c.calculation_id} · {c.created_at}</option>)}</select></label>
      <label>Due-date / timing snapshot (if applicable)<select disabled={disabled} value={inputs.interest_run_id || ''} onChange={e => invalidate({ ...inputs, interest_run_id: e.target.value || null })}><option value="">Not provided — timing may be undetermined</option>{interests.filter(i => i.calculation_id === inputs.calculation_id).map(i => <option key={i.interest_run_id} value={i.interest_run_id}>{i.interest_run_id} · {i.created_at}</option>)}</select></label>
      <label>Evidence version<select disabled={disabled} value={inputs.evidence_version_id || ''} onChange={e => void chooseVersion(e.target.value)}><option value="">No deposit evidence committed / selected</option>{versions.map(v => <option key={v.evidence_version_id} value={v.evidence_version_id}>V{v.version} · {v.source_file_name} · {v.validation_summary.total_rows} rows</option>)}</select></label></div>
      {!calculations.length && !loading && <InfoNotice tone="warning">Complete and save the TDS calculation before previewing deposit compliance. Refresh this page after the calculation is available.</InfoNotice>}
      <div className="wizard-actions"><button className="secondary-button" disabled={disabled || locked} onClick={() => fileInput.current?.click()}><Upload size={14} /> Upload Deposit Evidence</button><a className="secondary-button" href="#phase5-evidence">Review Evidence</a><a className="secondary-button" href="#phase5-policies">Configure / View Policy</a><button className="secondary-button" disabled={disabled || !inputs.calculation_id} onClick={() => void previewRun()}>Preview Compliance</button><button className="primary-button" disabled={disabled || !runReady} onClick={() => setConfirm(true)}>Run Compliance</button></div><p className="muted">Preview requires a saved calculation. A final run requires a resolved policy and a current preview. Missing evidence remains an explicit review outcome.</p>
    </section>
    <section className="workspace-section" id="phase5-evidence"><div className="section-heading"><div><span className="eyebrow">SOURCE EVIDENCE</span><h3>Deposit / Challan Evidence</h3><p className="muted">{selectedVersion ? `Evidence committed · V${selectedVersion.version} · ${selectedVersion.validation_summary.total_rows} rows · ${selectedVersion.source_file_name}` : 'No deposit evidence committed'}</p></div></div>
      <input ref={fileInput} aria-label="Deposit evidence file" type="file" accept=".csv,.xlsx,.xls" hidden onChange={e => { void upload(e.target.files?.[0]); e.target.value = ''; }} />
      <p className="muted">CSV, XLSX or XLS · Up to 25 MB · Source-provided deposit evidence. Each commit creates a separate version.</p>
      {filename && <p>Selected source: <b>{filename}</b></p>}
      <div className="wizard-actions"><button className="secondary-button" disabled={disabled || !uploadId || locked} onClick={() => void validate()}>Validate Evidence</button><button className="primary-button" disabled={disabled || !uploadId || !validation?.can_commit || locked} onClick={() => void commit()}>Commit Evidence</button></div>
      {validation && <><InfoNotice tone={validation.can_commit ? 'info' : 'warning'}>Validation: {validation.summary.total_rows} rows detected · {validation.summary.total_rows - validation.summary.invalid_rows - validation.summary.review_rows} valid · {validation.summary.invalid_rows} invalid · {validation.summary.review_rows} require review. {validation.can_commit ? 'Ready for explicit commit.' : 'Correct the source before committing.'}</InfoNotice>{validation.mapping_warnings.map((w, i) => <InfoNotice tone="warning" key={i}>{w}</InfoNotice>)}{!!validation.unmapped_headers.length && <InfoNotice tone="warning">Unmapped columns: {validation.unmapped_headers.join(', ')}</InfoNotice>}<h4>Validation preview · Not committed</h4><EvidenceTable key={uploadId} rows={validation.rows} /></>}
      <h4>Committed evidence</h4><EvidenceTable key={inputs.evidence_version_id || 'none'} rows={evidence} version={selectedVersion} />
    </section>
    <Policies assignment={assignment} policies={policies} busy={disabled} onCreate={createPolicy} onView={id => void perform('Loading policy', async () => { const p = await phase5Api.policy(a, id); if (mounted.current) setPolicyDetail(p); })} />
    <section className="workspace-section" ref={resultsRef}><div className="section-heading"><div><span className="eyebrow">{detail ? 'PERSISTED RESULTS' : 'PREVIEW'}</span><h3>{detail ? 'Historical Run · Read-only' : 'Compliance Preview'}</h3><p className="muted">{detail ? `${detail.deposit_run.deposit_run_id} · Created ${detail.deposit_run.created_at} · Evidence ${provided(detail.deposit_run.evidence_version_id)}` : 'Preview is not a committed historical run.'}</p></div></div>
      {detail && <p>Policy snapshot: {detail.deposit_run.phase5_rule_snapshot ? `${detail.deposit_run.phase5_rule_snapshot.policy_id} · V${detail.deposit_run.phase5_rule_snapshot.policy_version}` : 'See each liability’s policy / review reason'}</p>}
      {!detail && !preview ? <EmptyState title="No deposit-compliance result to display" text={history.length ? 'Open a saved run from the audit register below, or preview the evidence selected above.' : 'No saved compliance run exists yet. Select the review inputs, preview the result, then create a saved run when it is ready.'} /> : <>{!summary && detail ? <ErrorNotice message="Run saved, but its persisted summary could not be loaded." onRetry={() => void perform('Loading saved run', () => fetchRun(detail.deposit_run.deposit_run_id))} /> : <SummaryCards summary={summary} />}{warnings(rows).map(w => <InfoNotice key={w} tone="warning">{w}</InfoNotice>)}<ResultsTable key={detail?.deposit_run.deposit_run_id || 'preview'} rows={rows} context={context} onDetail={setRow} /></>}
    </section>
    <section className="workspace-section phase5-history"><div className="section-heading"><div><span className="eyebrow">AUDIT REGISTER</span><h3>Run History</h3></div></div>{!history.length ? <EmptyState title="No saved runs" text="No Phase 5 compliance run exists yet." /> : <div className="table-scroll"><table className="data-table"><thead><tr>{['Run ID', 'Run Version', 'Created At', 'Evidence Version', 'Policy Version', 'Status / Summary'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{history.map(h => <tr key={h.deposit_run_id}><td><button className="text-button" disabled={disabled} onClick={() => void perform('Loading saved run', async () => { await fetchRun(h.deposit_run_id); if (mounted.current) resultsRef.current?.scrollIntoView?.({ behavior: 'smooth' }); })}>{h.deposit_run_id}</button></td><td>{h.version == null ? 'Separate immutable run' : `V${h.version}`}</td><td>{h.created_at}</td><td>{versions.find(v => v.evidence_version_id === h.evidence_version_id)?.version ? `V${versions.find(v => v.evidence_version_id === h.evidence_version_id)?.version} · ` : ''}{provided(h.evidence_version_id)}</td><td>{h.phase5_rule_snapshot ? `V${h.phase5_rule_snapshot.policy_version} · ${h.phase5_rule_snapshot.policy_id}` : 'See result detail'}</td><td>Saved · Read-only<small className="phase5-block">{detail?.deposit_run.deposit_run_id === h.deposit_run_id && summary ? `${summary.compliant_count} compliant · ${summary.review_required_count} review · ${summary.exception_count} exceptions` : 'Open run for persisted summary'}</small></td></tr>)}</tbody></table></div>}</section>
    <DetailDrawer row={row} context={context} run={detail?.deposit_run} version={displayedVersion} onClose={() => setRow(null)} />
    <Dialog.Root open={confirm} onOpenChange={open => !busy && setConfirm(open)}><Dialog.Portal><Dialog.Overlay className="phase5-overlay" /><Dialog.Content className="phase5-detail"><header className="drawer-head"><div><Dialog.Title>Confirm New Compliance Run</Dialog.Title><Dialog.Description>Creates a separate immutable historical snapshot.</Dialog.Description></div></header><div className="drawer-body"><p>{assignment.assessee_legal_name} · {a} · FY {assignment.financial_year}</p><p>Evidence: {selectedVersion ? `V${selectedVersion.version} · ${selectedVersion.source_file_name}` : 'Not provided'}</p><p>Calculation: {inputs.calculation_id}</p><p>Interest run: {provided(inputs.interest_run_id)}</p><p>Applicable policies: {selectedPolicies.map(p => `${p.policy_id} · V${p.policy_version}`).join('; ') || 'Not Determinable'}</p><p>The server rechecks applicable policies when the run is created. Any changed configuration is preserved in the new snapshot.</p>{warnings(preview?.items || []).map(w => <InfoNotice key={w} tone="warning">{w}</InfoNotice>)}<div className="wizard-actions"><Dialog.Close className="secondary-button" disabled={!!busy}>Cancel</Dialog.Close><button className="primary-button" disabled={disabled || !runReady} onClick={() => void run()}>{busy || 'Confirm & Create Run'}</button></div></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={!!policyDetail} onOpenChange={open => !open && setPolicyDetail(null)}><Dialog.Portal><Dialog.Overlay className="phase5-overlay" /><Dialog.Content className="phase5-detail"><header className="drawer-head"><div><Dialog.Title>Policy Detail</Dialog.Title><Dialog.Description>{policyDetail?.policy_id} · V{policyDetail?.policy_version}</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="Close policy detail">×</Dialog.Close></header>{policyDetail && <div className="drawer-body"><Badge value={policyDetail.status} /><p>Active: {policyDetail.active ? 'Yes' : 'No'} · Priority {policyDetail.priority}</p><p>FY {policyDetail.financial_years.join(', ')} · Effective {policyDetail.effective_from} to {policyDetail.effective_to}</p><p>Assignment {policyDetail.assignment_id} · Client {policyDetail.client_id}</p><p>Identifiers: {policyDetail.authoritative_identifier_types.map(label).join(', ')}</p><p>Relationships: {policyDetail.permitted_relationship_types.map(label).join(', ')}</p><p>Allocation: {label(policyDetail.allocation_policy)}</p><p>Ambiguity: {label(policyDetail.ambiguity_policy)}</p><p>Created: {provided(policyDetail.created_at)} · Actor: {provided(policyDetail.created_by)}</p></div>}</Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
