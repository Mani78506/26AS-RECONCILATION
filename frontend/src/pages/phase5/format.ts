import axios from 'axios';
import type { DepositResult } from './types';
export const labels: Record<string, string> = {
  FULLY_DEPOSIT_COMPLIANT: 'Compliant', DEPOSIT_MATCHED: 'Deposit Matched', DEPOSIT_SHORT: 'Short Deposit', DEPOSIT_EXCESS: 'Excess Deposit',
  MISSING_DEPOSIT_EVIDENCE: 'Deposit evidence not provided', DEPOSIT_NOT_DETERMINABLE: 'Not Determinable',
  DEPOSIT_ON_TIME: 'On Time', DEPOSIT_LATE: 'Late Deposit', DEPOSIT_DATE_NOT_PROVIDED: 'Deposit date not provided',
  DUE_DATE_NOT_DETERMINABLE: 'Due date not determinable', INVALID_DATE_SEQUENCE: 'Invalid Date Sequence',
  DEPOSIT_AMOUNT_EXCEPTION: 'Amount Exception', DEPOSIT_TIMELINESS_EXCEPTION: 'Timeliness Exception', DEPOSIT_AND_TIMELINESS_EXCEPTION: 'Amount and Timeliness Exception',
  REVIEW_REQUIRED: 'Review Required', NOT_DETERMINABLE: 'Not Determinable', ESTABLISHED: 'Authoritative Mapping',
  PHASE5_POLICY_NOT_FOUND: 'Phase 5 policy configuration required', PHASE5_POLICY_AMBIGUOUS: 'Multiple applicable policies require review',
  AMBIGUOUS_DEPOSIT_MAPPING: 'Deposit evidence could not be uniquely mapped', EXACT_TRANSACTION_REFERENCE: 'Exact Transaction Reference',
  EXACT_REFERENCE_ONLY: 'Exact Reference Only', ONE_TO_ONE: 'One Liability to One Evidence', ONE_TO_MANY: 'One Liability to Many Evidence',
};
export const label = (value?: string | null) => value ? labels[value] || value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Not Provided';
export const provided = (value?: string | number | null) => value == null || value === '' ? 'Not Provided' : String(value);
export const money = (value?: number | null) => value == null ? 'Not Determinable' : `?${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
export const phase5Error = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 401 || error.response?.status === 403) return 'Access denied. Your office administrator can check your permissions for this assignment.';
    if (error.response?.status === 501 || error.response?.status === 503) return 'Secure compliance access is not available. Your office administrator must configure authentication.';
    if (error.response?.status === 422) return 'The submitted data is invalid. Check the required fields, scope, financial year and effective dates.';
    if (error.response?.status === 409) return 'This assignment is locked or the selected inputs are no longer available. Refresh before continuing.';
  }
  return 'Unable to load deposit compliance data. Please try again.';
};
export function warnings(rows: DepositResult[]) {
  const messages = new Set<string>();
  rows.forEach(r => {
    if (r.reason_code) messages.add(label(r.reason_code));
    if (r.deposit_status === 'MISSING_DEPOSIT_EVIDENCE') messages.add('Deposit evidence not provided');
    if (r.deposited_tds == null && r.deposit_status !== 'MISSING_DEPOSIT_EVIDENCE') messages.add('Deposit amount not provided or not determinable');
    if (r.timeliness_status === 'INVALID_DATE_SEQUENCE') messages.add('Deposit date precedes deduction date — Manual Review Required');
    if (r.overall_status === 'REVIEW_REQUIRED' && r.reason) messages.add(r.reason);
  });
  return Array.from(messages);
}
