import type { ReconciliationResult } from "../types";

export type StatusPresentation = { label: string; shortLabel: string; tone: string; explanation: string; action: string };

const base: Record<string, StatusPresentation> = {
  MATCHED_CLAIMABLE: { label: "Matched", shortLabel: "Matched", tone: "green", explanation: "Books TDS Expected and 26AS TDS Deducted agree for this reconciliation result.", action: "No reconciliation action is required. Verify supporting documents before filing or claiming." },
  MATCHED_NOT_CLAIMABLE: { label: "Matched · Not Claimable", shortLabel: "Not Claimable", tone: "amber", explanation: "The TDS amount matches the 26AS entry, but the reported 26AS status does not currently make the credit claimable.", action: "Review the 26AS status and deductor filing. Claim only when the entry becomes eligible." },
  AMOUNT_MISMATCH: { label: "Amount Mismatch", shortLabel: "Mismatch", tone: "red", explanation: "A corresponding 26AS entry was found, but its TDS amount differs from Books.", action: "Verify the Books entry, deductor statement, and 26AS details." },
  MISSING_IN_26AS: { label: "Missing in 26AS", shortLabel: "Missing in 26AS", tone: "red", explanation: "A Books TDS entry could not be found in 26AS for this reconciliation run.", action: "Verify whether the deductor has filed or deposited TDS and whether the entry is expected in 26AS." },
  MISSING_IN_BOOKS: { label: "Missing in Books", shortLabel: "Missing in Books", tone: "amber", explanation: "A 26AS TDS entry exists but no corresponding Books entry was found.", action: "Verify whether the TDS receipt has been recorded in the client’s Books." },
  IDENTITY_UNMAPPED: { label: "Deductor Not Mapped", shortLabel: "Deductor Unmapped", tone: "purple", explanation: "The TAN could not be authoritatively mapped using the available identity data. This does not mean the TAN is invalid.", action: "Review TAN and deductor details, then update the Customer Master if appropriate." },
  DUPLICATE_BOOK: { label: "Duplicate in Books", shortLabel: "Duplicate Books", tone: "slate", explanation: "Multiple Books records appear to represent the same reconciliation item.", action: "Review duplicate Books entries before claiming or reconciling TDS." },
  DUPLICATE_26AS: { label: "Duplicate in 26AS", shortLabel: "Duplicate 26AS", tone: "slate", explanation: "Multiple 26AS records appear to represent the same reconciliation item.", action: "Review the 26AS entries and supporting deductor information." },
};

export function getReconciliationStatusPresentation(result: Pick<ReconciliationResult, "result" | "match_method" | "status" | "reason" | "recommended_action">): StatusPresentation {
  if (result.match_method === "GROUP_SAME_QUARTER" || result.match_method === "GROUP_OUTSIDE_QUARTER") return {
    label: result.match_method === "GROUP_OUTSIDE_QUARTER" ? "Group Matched · Different Quarter" : "Group Matched", shortLabel: "Group Matched", tone: "blue",
    explanation: "Multiple Books and/or 26AS records together reconcile to the same TDS amount.", action: result.recommended_action || "Review the grouped transactions together.",
  };
  if (result.match_method === "EXACT_1_TO_1_OUTSIDE_QUARTER") return {
    label: "Matched · Different Quarter", shortLabel: "Different Quarter", tone: "blue",
    explanation: "Books and 26AS TDS amounts match, but they appear in different financial quarters.", action: "Review transaction dates and the quarter in which TDS was reported.",
  };
  const presentation = base[result.result] || { label: result.result, shortLabel: result.result, tone: "slate", explanation: "The reconciliation engine classified this result.", action: "Review the engine reason and supporting records." };
  if (result.result === "MATCHED_NOT_CLAIMABLE" && result.status) {
    return { ...presentation, explanation: `The TDS amount matches, but 26AS status ${result.status} does not currently make this credit claimable.` };
  }
  return { ...presentation, action: result.recommended_action || presentation.action };
}
