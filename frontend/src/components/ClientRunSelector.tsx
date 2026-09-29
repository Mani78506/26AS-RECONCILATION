import { useEffect, useMemo, useState } from "react";
import { Loader2, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api, getErrorMessage } from "../services/api";
import type { WorkspaceClient } from "../types";

export function ClientRunSelector({ open, initialClientId, onClose, onSelectClient, onSelectRun, onStartNewClient }: { open: boolean; initialClientId?: string | null; onClose: () => void; onSelectClient: (id: string) => void; onSelectRun: (id: string) => void; onStartNewClient: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<WorkspaceClient | null>(null);
  const clients = useQuery({ queryKey: ["workspace-clients"], queryFn: api.clients, enabled: open, staleTime: 15_000 });
  const runs = useQuery({ queryKey: ["workspace-client-runs", selected?.client_id], queryFn: () => api.clientRuns(selected!.client_id), enabled: open && !!selected });
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected(initialClientId ? (clients.data || []).find((client) => client.client_id === initialClientId) || null : null);
  }, [open, initialClientId, clients.data]);
  const visible = useMemo(() => (clients.data || []).filter((item) => `${item.client_name} ${item.assessee_pan}`.toLowerCase().includes(query.toLowerCase())), [clients.data, query]);
  if (!open) return null;
  // Client selection is a preview step. Do not clear the current dashboard
  // until the CA has selected one of this client's completed runs.
  const chooseClient = (client: WorkspaceClient) => { setSelected(client); };
  return <div className="drawer-root" data-testid="client-run-selector"><div className="drawer-backdrop" onClick={onClose} aria-hidden="true" /><section className="client-selector-modal" role="dialog" aria-modal="true" aria-label="Select Client and Reconciliation Run" onKeyDown={(e) => e.key === "Escape" && onClose()}><header className="client-selector-head"><div><span className="eyebrow">WORKSPACE</span><h2>{selected ? selected.client_name : "Select a client"}</h2><p>{selected ? `PAN: ${selected.assessee_pan || "Client information unavailable"}` : "Choose a client to view its reconciliation runs."}</p></div><button className="icon-button" aria-label="Close client selector" onClick={onClose} autoFocus><X size={18} /></button></header><div className="client-selector-body">
    {!selected ? <><label className="client-search"><Search size={15} /><input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search clients by name or PAN…" aria-label="Search clients by name or PAN" /></label>{clients.isLoading ? <p className="selector-status"><Loader2 className="spin" size={15} /> Loading clients…</p> : clients.isError ? <div className="api-notice"><span>Unable to load clients.</span><button className="text-button" onClick={() => clients.refetch()}>Retry</button></div> : !visible.length ? <div className="empty-state"><h3>{clients.data?.length ? "No matching clients" : "No clients available yet"}</h3><p>{clients.data?.length ? "Try a different client name or PAN." : "Upload and complete a reconciliation run to create a workspace."}</p></div> : <div className="client-list">{visible.map((client) => <button key={client.client_id} className="client-card" onClick={() => chooseClient(client)}><b>{client.client_name}</b><small>PAN: {client.assessee_pan || "Client information unavailable"}</small><span>{client.financial_years.join(", ") || "FY not recorded"} · {client.completed_run_count} completed run{client.completed_run_count === 1 ? "" : "s"} · View runs →</span></button>)}</div>}</>
      : <><button className="text-button" onClick={() => setSelected(null)}>← Back to clients</button><h3 className="run-list-title">Reconciliation runs</h3><button className="secondary-button client-new-run-button" onClick={() => onStartNewClient(selected.client_id)}>New reconciliation for this client</button>{runs.isLoading ? <p className="selector-status"><Loader2 className="spin" size={15} /> Loading reconciliation runs…</p> : runs.isError ? <div className="api-notice"><span>{getErrorMessage(runs.error, "Unable to load runs.")}</span><button className="text-button" onClick={() => runs.refetch()}>Retry</button></div> : !(runs.data?.runs || []).some((run) => run.status === "COMPLETED") ? <div className="empty-state"><h3>No completed reconciliation runs</h3><p>Complete a reconciliation for this client to open a workspace.</p></div> : <div className="client-list">{(runs.data?.runs || []).map((run) => <button key={run.run_id} className="client-card" disabled={run.status !== "COMPLETED"} onClick={() => { onSelectClient(selected.client_id); onSelectRun(run.run_id); onClose(); }}><b>FY {run.financial_year || "—"} · {run.status === "COMPLETED" ? "Completed" : run.status}</b><small>Reconciled: {new Date(run.finished_at || run.created_at).toLocaleString()}</small><span>{run.summary?.books_count ?? 0} Books · {run.summary?.statement_count ?? 0} 26AS {run.status === "COMPLETED" ? "· Open workspace →" : "· Complete processing before opening"}</span></button>)}</div>}</>}
  </div></section></div>;
}
