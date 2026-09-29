import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Columns3, Database, RotateCcw, Save, Server, ShieldCheck, SlidersHorizontal, UserRound, XCircle } from "lucide-react";
import { toast } from "sonner";
import { ErrorNotice, InfoNotice, PageHeader, Skeleton } from "../components/common";
import { useWorkspace } from "../context/WorkspaceContext";
import { fmtDateTime } from "../lib/format";
import { api, getErrorMessage } from "../services/api";
import type { Settings } from "../types";

type NumericSetting = Exclude<keyof Settings, "tds_rules">;
const FIELDS: { key: NumericSetting; label: string; hint: string; step: number; min: number; max: number; unit?: string }[] = [
  { key: "amount_tolerance_abs", label: "Absolute amount tolerance", hint: "Books vs 26AS difference (₹) treated as matched.", step: 1, min: 0, max: 100000, unit: "₹" },
  { key: "amount_tolerance_pct", label: "Percentage tolerance", hint: "Applied as % of the books amount; the larger of the two tolerances is used.", step: 0.1, min: 0, max: 100, unit: "%" },
  { key: "fuzzy_high_threshold", label: "Auto-map similarity threshold", hint: "Name similarity at or above this is mapped as HIGH CONFIDENCE (still listed for review).", step: 1, min: 50, max: 100, unit: "%" },
  { key: "fuzzy_review_threshold", label: "Review similarity threshold", hint: "Between this and the auto-map threshold a suggestion is shown as REVIEW REQUIRED.", step: 1, min: 50, max: 100, unit: "%" },
  { key: "fuzzy_min_gap", label: "Minimum confidence gap", hint: "Best candidate must lead the runner-up by this margin to auto-map.", step: 1, min: 0, max: 50, unit: "%" },
  { key: "max_group_size", label: "Maximum group size", hint: "Largest combination of entries searched when grouping many-to-one.", step: 1, min: 2, max: 8 },
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { user, run, logout } = useWorkspace();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30000, retry: 1 });
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<Settings | null>(null);
  useEffect(() => { if (q.data && !form) setForm(q.data.settings); }, [q.data, form]);
  const save = useMutation({ mutationFn: (body: Partial<Settings>) => api.updateSettings(body), onSuccess: (d) => { setForm(d.settings); qc.setQueryData(["settings"], d); toast.success("Reconciliation preferences saved", { description: "They apply to the next run or re-run." }); }, onError: (e) => toast.error(getErrorMessage(e, "Settings could not be saved.")) });
  const dirty = !!form && !!q.data && JSON.stringify(form) !== JSON.stringify(q.data.settings);
  const [pan, setPan] = useState(() => localStorage.getItem("26as-mask-pan") !== "false");
  const [density, setDensity] = useState(() => localStorage.getItem("26as-density") || "compact");
  useEffect(() => { localStorage.setItem("26as-mask-pan", String(pan)); document.documentElement.dataset.maskPan = String(pan); }, [pan]);
  useEffect(() => { localStorage.setItem("26as-density", density); document.documentElement.dataset.density = density; }, [density]);

  return <>
    <PageHeader eyebrow="WORKSPACE" title="Settings" subtitle="Service status, reconciliation preferences, display and security options." />
    <div className="settings-grid">
      <section className="workspace-section" data-testid="settings-api-status"><div className="section-heading"><div><span className="eyebrow">API STATUS</span><h2><Server size={15} /> Reconciliation service</h2></div>{health.isLoading ? <Skeleton h={20} w={80} /> : health.data ? <span className="badge green" data-testid="api-status-badge"><CheckCircle2 size={11} /> Connected</span> : <span className="badge red" data-testid="api-status-badge"><XCircle size={11} /> Unavailable</span>}</div>
        <div className="detail-fields compact"><div className="field"><span>Endpoint</span><b className="mono ellipsis">{process.env.REACT_APP_BACKEND_URL}/api</b></div><div className="field"><span>Engine</span><b>{health.data?.service || "—"} {health.data?.version ? `v${health.data.version}` : ""}</b></div><div className="field"><span>Database</span><b>{health.data ? (health.data.database === "ok" ? "MongoDB connected" : "MongoDB unavailable") : "—"}</b></div><div className="field"><span>Last check</span><b>{health.dataUpdatedAt ? fmtDateTime(new Date(health.dataUpdatedAt).toISOString()) : "—"}</b></div></div>
        {health.isError && <ErrorNotice message="Reconciliation service is unavailable. Check the backend connection and try again." onRetry={() => health.refetch()} />}</section>

      <section className="workspace-section" data-testid="settings-workspace"><div className="section-heading"><div><span className="eyebrow">WORKSPACE</span><h2><Database size={15} /> Active run</h2></div></div>
        {run ? <div className="detail-fields compact"><div className="field"><span>Assessee</span><b>{run.assessee_name || "—"}</b></div><div className="field"><span>Financial year</span><b>{run.financial_year || "—"}</b></div><div className="field"><span>Run ID</span><b className="mono">{run.run_id}</b></div><div className="field"><span>Source</span><b>{run.source === "sample" ? "Sample dataset" : "Uploaded files"}</b></div><div className="field"><span>Processed</span><b>{fmtDateTime(run.finished_at)}</b></div><div className="field"><span>Re-runs</span><b>{run.rerun_count}</b></div></div> : <p className="muted-copy">No run selected. Process a reconciliation to populate the workspace.</p>}</section>

      <section className="workspace-section span-2" data-testid="settings-preferences"><div className="section-heading"><div><span className="eyebrow">RECONCILIATION PREFERENCES</span><h2><SlidersHorizontal size={15} /> Matching tolerances & identity thresholds</h2></div><div className="page-actions"><button className="secondary-button" data-testid="settings-reset-button" disabled={!q.data || save.isPending} onClick={() => q.data && setForm(q.data.defaults)}><RotateCcw size={13} /> Defaults</button><button className="primary-button" data-testid="settings-save-button" disabled={!dirty || save.isPending} onClick={() => form && save.mutate(form)}><Save size={14} /> {save.isPending ? "Saving…" : "Save"}</button></div></div>
        <InfoNotice>These values are read by the Python engine at run time. Changing them does not alter completed results — re-run the reconciliation to apply them.</InfoNotice>
        {q.isError && <ErrorNotice message={getErrorMessage(q.error)} onRetry={() => q.refetch()} />}
        {form ? <div className="form-grid three">{FIELDS.map((f) => <label key={f.key}>{f.label}<div className="input-unit">{f.unit && <span>{f.unit}</span>}<input type="number" data-testid={`setting-${f.key}`} step={f.step} min={f.min} max={f.max} value={form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: Number(e.target.value) })} /></div><small>{f.hint}</small></label>)}</div> : <div className="form-grid three">{FIELDS.map((f) => <Skeleton key={f.key} h={54} />)}</div>}</section>

      <section className="workspace-section" data-testid="settings-display"><div className="section-heading"><div><span className="eyebrow">DISPLAY</span><h2><Columns3 size={15} /> Display</h2></div></div>
        <label className="toggle-row"><span>Table density</span><select data-testid="density-select" value={density} onChange={(e) => setDensity(e.target.value)}><option value="compact">Compact</option><option value="comfortable">Comfortable</option></select></label>
        <p className="muted-copy">Amounts always use Indian formatting (₹1,25,000). Column visibility is remembered per browser on the Reconciliation page.</p></section>

      <section className="workspace-section" data-testid="settings-security"><div className="section-heading"><div><span className="eyebrow">SECURITY</span><h2><ShieldCheck size={15} /> Security & user</h2></div></div>
        <div className="detail-fields compact"><div className="field"><span>Signed in as</span><b><UserRound size={12} /> {user.name}</b></div><div className="field"><span>Role</span><b>{user.role}</b></div></div>
        <label className="toggle-row"><span>Mask PAN in list views (ABCDE****F)</span><input type="checkbox" data-testid="mask-pan-toggle" checked={pan} onChange={(e) => setPan(e.target.checked)} /></label>
        <p className="muted-copy">Full PAN is shown only in the transaction detail view. Raw server errors and file paths are never displayed in this workspace.</p>
        <button className="secondary-button danger" data-testid="settings-logout-button" onClick={logout}>Sign out</button></section>
    </div>
  </>;
}
