import { client } from '../../services/api';
import type { InterestComplianceResult, UploadInfo } from '../../types';
import type { CalculationRun, Evidence, EvidenceValidation, EvidenceVersion, Inputs, InterestRun, Liability, Policy, PolicyInput, Preview, RunDetail, RunResponse, DepositRun, Summary } from './types';
const base = (assignment: string) => `/tds-compliance/assignments/${encodeURIComponent(assignment)}`;
const id = encodeURIComponent;
export const phase5Api = {
  versions: (a: string) => client.get<{ items: EvidenceVersion[] }>(`${base(a)}/deposit-evidence`).then(r => r.data.items),
  evidence: (a: string, v: string) => client.get<{ evidence_version: EvidenceVersion; items: Evidence[] }>(`${base(a)}/deposit-evidence/${id(v)}`).then(r => r.data),
  upload: (a: string, file: File) => { const form = new FormData(); form.append('file', file); return client.post<UploadInfo>(`${base(a)}/deposit-evidence/upload`, form, { timeout: 120000 }).then(r => r.data); },
  validate: (a: string, upload: string) => client.post<EvidenceValidation>(`${base(a)}/deposit-evidence/validate`, { upload_id: upload }).then(r => r.data),
  commit: (a: string, upload: string) => client.post<{ evidence_version: EvidenceVersion }>(`${base(a)}/deposit-evidence/commit`, { upload_id: upload }).then(r => r.data.evidence_version),
  policies: (a: string) => client.get<{ items: Policy[] }>(`${base(a)}/policies`).then(r => r.data.items),
  policy: (a: string, p: string) => client.get<Policy>(`${base(a)}/policies/${id(p)}`).then(r => r.data),
  createPolicy: (a: string, body: PolicyInput) => client.post<Policy>(`${base(a)}/policies`, body).then(r => r.data),
  preview: (a: string, body: Inputs) => client.post<Preview>(`${base(a)}/deposit-compliance/preview`, body).then(r => r.data),
  run: (a: string, body: Inputs) => client.post<RunResponse>(`${base(a)}/deposit-compliance/run`, body).then(r => r.data),
  history: (a: string) => client.get<{ items: DepositRun[] }>(`${base(a)}/deposit-compliance/history`).then(r => r.data.items),
  detail: (a: string, r: string) => client.get<RunDetail>(`${base(a)}/deposit-compliance/${id(r)}`).then(r => r.data),
  summary: (a: string, r: string) => client.get<{ summary: Summary }>(`${base(a)}/deposit-compliance/${id(r)}/summary`).then(r => r.data.summary),
  calculations: (a: string) => client.get<{ items: CalculationRun[] }>(`${base(a)}/calculations`).then(r => r.data.items),
  calculation: (a: string, c: string) => client.get<{ items: Liability[] }>(`${base(a)}/calculations/${id(c)}`).then(r => r.data.items),
  interests: (a: string) => client.get<{ items: InterestRun[] }>(`${base(a)}/interest/history`).then(r => r.data.items),
  interest: (a: string, i: string) => client.get<{ items: InterestComplianceResult[] }>(`${base(a)}/interest/${id(i)}`).then(r => r.data.items),
};
