import { useState } from 'react';
import { InfoNotice } from '../../components/common';
import type { TdsComplianceAssignment } from '../../types';
import type { Policy, PolicyInput } from './types';
import { Badge } from './components';
import { label, provided } from './format';

export default function Policies({ assignment, policies, busy, onCreate, onView }: { assignment: TdsComplianceAssignment; policies: Policy[]; busy: boolean; onCreate: (p: PolicyInput) => Promise<boolean>; onView: (id: string) => void }) {
  const [creating, setCreating] = useState(false);
  const [dates, setDates] = useState({ from: '', to: '', priority: '', state: 'DRAFT', active: false, identifiers: false, relationships: '', allocation: '', ambiguity: '' });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const saved = await onCreate({ assignment_id: assignment.assignment_id, client_id: assignment.client_id, financial_years: [assignment.financial_year], effective_from: dates.from, effective_to: dates.to, priority: Number(dates.priority), active: dates.active, status: dates.state, authoritative_identifier_types: dates.identifiers ? ['EXACT_TRANSACTION_REFERENCE'] : [], permitted_relationship_types: dates.relationships === 'both' ? ['ONE_TO_ONE', 'ONE_TO_MANY'] : [dates.relationships], allocation_policy: dates.allocation, ambiguity_policy: dates.ambiguity });
    if (saved) setCreating(false);
  }
  return <section className="workspace-section" id="phase5-policies"><div className="section-heading"><div><span className="eyebrow">CONFIGURATION</span><h3>Phase 5 Policies</h3><p className="muted">Policies are scoped to {assignment.assessee_legal_name} · {assignment.assignment_id} · FY {assignment.financial_year}. Office permissions are checked when saving.</p></div><button className="secondary-button" disabled={busy} onClick={() => setCreating(!creating)}>{creating ? 'Cancel policy' : 'Create Policy'}</button></div>
    {!policies.length && <InfoNotice tone="warning">Configuration Required. Phase 5 policy configuration is required before compliance can be run.</InfoNotice>}
    {!!policies.length && <div className="table-scroll"><table className="data-table"><thead><tr>{['Policy / Version', 'FY', 'Effective Dates', 'Priority', 'Approval', 'Active', 'Actor'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{policies.map(p => <tr key={p.policy_id}><td><button className="text-button" onClick={() => onView(p.policy_id)} disabled={busy}>{p.policy_id} · V{p.policy_version}</button></td><td>{p.financial_years.join(', ')}</td><td>{p.effective_from} to {p.effective_to}</td><td>{p.priority}</td><td><Badge value={p.status} /></td><td>{p.active ? 'Yes' : 'No'}</td><td>{provided(p.created_by)}</td></tr>)}</tbody></table></div>}
    {creating && <form className="form-grid phase5-policy-form" onSubmit={submit}>
      <label>Effective from<input type="date" required value={dates.from} onChange={e => setDates({ ...dates, from: e.target.value })} /></label><label>Effective to<input type="date" required min={dates.from} value={dates.to} onChange={e => setDates({ ...dates, to: e.target.value })} /></label>
      <label>Priority<input type="number" step="1" required value={dates.priority} onChange={e => setDates({ ...dates, priority: e.target.value })} /></label>
      <label>Approval state<select value={dates.state} onChange={e => setDates({ ...dates, state: e.target.value, active: false })}>{['DRAFT', 'APPROVED', 'ACTIVE', 'RETIRED'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
      <label className="phase5-check"><input type="checkbox" disabled={!['APPROVED', 'ACTIVE'].includes(dates.state)} checked={dates.active} onChange={e => setDates({ ...dates, active: e.target.checked })} />Active for selection</label>
      <label className="phase5-check"><input type="checkbox" required checked={dates.identifiers} onChange={e => setDates({ ...dates, identifiers: e.target.checked })} />Use exact transaction references as authoritative identifiers</label>
      <label>Permitted relationships<select required value={dates.relationships} onChange={e => setDates({ ...dates, relationships: e.target.value })}><option value="">Select a configuration</option><option value="ONE_TO_ONE">One liability to one evidence</option><option value="ONE_TO_MANY">One liability to many evidence</option><option value="both">Both supported relationships</option></select></label>
      <label>Allocation policy<select required value={dates.allocation} onChange={e => setDates({ ...dates, allocation: e.target.value })}><option value="">Select a configuration</option><option value="EXACT_REFERENCE_ONLY">Exact Reference Only</option></select></label>
      <label>Ambiguity policy<select required value={dates.ambiguity} onChange={e => setDates({ ...dates, ambiguity: e.target.value })}><option value="">Select a configuration</option><option value="REVIEW_REQUIRED">Require Manual Review</option></select></label>
      <div className="wizard-actions"><button type="submit" className="primary-button" disabled={busy}>Save Policy</button></div>
    </form>}
  </section>;
}
