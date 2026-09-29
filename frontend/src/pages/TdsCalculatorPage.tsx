import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { InfoNotice, PageHeader } from "../components/common";
import { api } from "../services/api";
import type { TdsComplianceAssignment } from "../types";
import TdsCalculatorWorkspace from "./TdsCalculatorWorkspace";

const standaloneAssignment: TdsComplianceAssignment = { assignment_id: "STANDALONE-CALCULATOR", organization_id: "TEST_ORG", client_id: "", assessee_legal_name: "TDS Calculator", financial_year: "2025-26", workflow: "TDS_COMPLIANCE", status: "DRAFT", created_at: "", updated_at: "", version: 1 };

export default function TdsCalculatorPage() {
  const assignments = useQuery({ queryKey: ["tds-compliance-assignments"], queryFn: api.tdsComplianceAssignments });
  const [assignmentId, setAssignmentId] = useState("");
  const selected = assignments.data?.find(item => item.assignment_id === assignmentId) || null;
  useEffect(() => { if (!assignmentId && assignments.data?.length) setAssignmentId(assignments.data[0].assignment_id); }, [assignmentId, assignments.data]);
  return <><PageHeader eyebrow="TDS COMPLIANCE" title="TDS Calculator" subtitle="Calculate applicable TDS from payment details using the approved statutory rule library." />
    <section className="workspace-section"><div className="section-heading"><div><span className="eyebrow">CALCULATION CONTEXT</span><h2>Transaction calculation</h2><p className="muted">Statutory rates and thresholds are selected from active approved rules. Select an assignment when an auditable saved calculation is needed.</p></div></div>
      {assignments.data?.length ? <label className="assignment-picker">Assignment for saved calculations<select value={assignmentId} onChange={event => setAssignmentId(event.target.value)}><option value="">Calculate without saving</option>{assignments.data.map(item => <option key={item.assignment_id} value={item.assignment_id}>{item.assessee_legal_name} · FY {item.financial_year}</option>)}</select></label> : <InfoNotice tone="info">When no active approved rule exists, the calculator remains not determinable. Configure statutory rules once in TDS Rule Configuration.</InfoNotice>}
    </section>
    <TdsCalculatorWorkspace assignment={selected || standaloneAssignment} presentationMode={!selected} />
  </>;
}
