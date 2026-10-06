import type { InterestComplianceResult, TdsCalculationResult } from '../../types';

export interface Evidence {
  evidence_id?: string | null; deposit_transaction_id?: string | null; tan?: string | null;
  challan_number?: string | null; cin?: string | null; bsr_code?: string | null;
  deposit_date?: string | null; challan_date?: string | null; tds_amount?: number | null;
  amount_deposited?: number | null; total_amount?: number | null; financial_year?: string | null;
  source_reference?: string | null; source_reference_status?: string | null; source_file_id?: string | null;
  source_file_name?: string | null; source_row_number?: number; evidence_version_id?: string;
  validation_status: string; validation_issues?: string[];
}
export interface EvidenceCounts { total_rows: number; invalid_rows: number; review_rows: number }
export interface EvidenceVersion {
  evidence_version_id: string; assignment_id: string; client_id: string; version: number;
  source_file_id: string; source_file_name: string; committed_at: string; committed_by?: string | null;
  validation_summary: EvidenceCounts;
}
export interface EvidenceValidation {
  file: { filename: string; upload_id?: string }; rows: Evidence[]; preview_rows?: Evidence[];
  mapped_headers: string[]; unmapped_headers: string[]; mapping_warnings: string[];
  summary: EvidenceCounts; can_commit: boolean;
}
export interface PolicyInput {
  assignment_id: string; client_id: string; financial_years: string[]; effective_from: string;
  effective_to: string; priority: number; active: boolean; status: string;
  authoritative_identifier_types: string[]; permitted_relationship_types: string[];
  allocation_policy: string; ambiguity_policy: string;
}
export interface Policy extends PolicyInput { policy_id: string; policy_version: string; created_at?: string; created_by?: string }
export interface Relationship {
  relationship_id?: string; relationship_status?: string; liability_ids?: string[]; evidence_ids?: string[];
  candidate_evidence_ids?: string[]; match_method?: string; allocation?: number | null;
}
export interface DepositResult extends Relationship {
  transaction_id: string; expected_tds: number | null; actual_tds_deducted: number | null;
  deposited_tds: number | null; deposit_difference: number | null; deposit_status: string;
  actual_deduction_date?: string | null; deposit_due_date?: string | null;
  timeliness_status: string; interest_status: string; overall_status: string; reason?: string;
  reason_code?: string; recommended_action?: string; evidence_entries: Evidence[];
  evidence_version_id?: string | null; phase5_rule_snapshot?: Policy | null;
  calculation_id?: string; group_size?: number;
}
export interface Summary {
  liability_count: number; evidence_count: number; matched_count: number; reconciled_count: number;
  review_required_count: number; ambiguous_count: number; unmatched_count: number;
  invalid_date_sequence_count: number; compliant_count: number; exception_count: number;
  not_determinable_count: number; expected_tds_total: number | null;
  actual_tds_deducted_total: number | null; deposited_tds_total: number | null;
  run_id?: string; deposit_run_id?: string; assignment_id?: string; client_id?: string;
}
export interface DepositRun {
  deposit_run_id: string; assignment_id: string; client_id: string; calculation_id: string;
  interest_run_id?: string | null; evidence_version_id?: string | null; ledger_version_id?: string;
  phase5_rule_snapshot?: Policy | null; created_at: string; created_by?: string | null; version?: number;
}
export interface Inputs { calculation_id: string; interest_run_id: string | null; evidence_version_id: string | null }
export interface Preview { items: DepositResult[]; summary: Summary; evidence_version_id?: string | null }
export interface RunDetail { deposit_run: DepositRun; items: DepositResult[] }
export interface RunResponse extends RunDetail { summary: Summary }
export interface CalculationRun { calculation_id: string; created_at: string; ledger_version_id?: string }
export interface InterestRun { interest_run_id: string; calculation_id: string; created_at: string }
export interface Liability extends TdsCalculationResult { financial_year?: string; tan?: string | null; source_reference?: string | null; invoice_number?: string | null; payment_date?: string | null }
export interface Context { liabilities: Liability[]; timing: InterestComplianceResult[] }
