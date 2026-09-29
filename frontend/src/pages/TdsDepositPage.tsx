import { useEffect, useState } from "react";
import { Landmark } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { InfoNotice, PageHeader } from "../components/common";
import { useSearchParams } from "react-router-dom";
import { api } from "../services/api";
import type { TdsComplianceAssignment } from "../types";
import Phase5Workspace from "./phase5/Phase5Workspace";

export default function TdsDepositPage() {
  const assignments = useQuery({ queryKey: ["tds-compliance-assignments"], queryFn: api.tdsComplianceAssignments });
  const [assignmentId, setAssignmentId] = useState("");
  const [searchParams] = useSearchParams();
  useEffect(() => { const requested = searchParams.get("assignment"); if (requested && assignments.data?.some(item => item.assignment_id === requested) && requested !== assignmentId) setAssignmentId(requested); else if (!assignmentId && assignments.data?.length) setAssignmentId(assignments.data[0].assignment_id); }, [assignmentId, assignments.data, searchParams]);
  const selected = assignments.data?.find(item => item.assignment_id === assignmentId) as TdsComplianceAssignment | undefined;
  return <>
    <PageHeader eyebrow="PHASE 5 · DEPOSIT COMPLIANCE" title="TDS Deposit / Challan Compliance" subtitle="A dedicated challan-evidence, deposit matching and exception-review workspace." />
    <section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">ASSIGNMENT CONTEXT</span><h2>Select the compliance assignment</h2><p className="muted">Deposit compliance uses immutable payment calculations and source challan evidence from the selected assignment.</p></div><Landmark size={20} /></div>
      {assignments.data?.length ? <label className="assignment-picker">Assignment<select value={assignmentId} onChange={event => setAssignmentId(event.target.value)}>{assignments.data.map(item => <option key={item.assignment_id} value={item.assignment_id}>{item.assessee_legal_name} · FY {item.financial_year}</option>)}</select></label> : <InfoNotice tone="info">Create an assignment from the Assignments screen before starting deposit and challan compliance.</InfoNotice>}
    </section>
    {selected && <><div className="assignment-page-context"><b>{selected.assessee_legal_name}</b><span>TAN {selected.tan || "Not provided"}</span><span>FY {selected.financial_year} · {selected.quarter || "All quarters"}</span><span>{selected.status}</span></div><Phase5Workspace key={selected.assignment_id} assignment={selected} /></>}
  </>;
}
