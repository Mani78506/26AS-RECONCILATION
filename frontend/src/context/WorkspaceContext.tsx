import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../services/api";
import type { Run } from "../types";

interface SessionUser { name: string; initials: string; role: string; }
interface WorkspaceValue {
  user: SessionUser; login: (name: string) => void; logout: () => void;
  runs: Run[]; run: Run | null; runId: string | null; clientId: string | null; selectClient: (id: string | null) => void; selectRun: (id: string | null) => void;
  workspaceHydrating: boolean; runLoading: boolean; runError: boolean; refreshRun: () => void;
  watchJob: (jobId: string, onDone?: (run: Run) => void) => void; processing: boolean;
}

const Ctx = createContext<WorkspaceValue | null>(null);
const SESSION_KEY = "26as-session";
const RUN_KEY = "26as-run-id";
const CLIENT_KEY = "26as-client-id";
const readSession = (): SessionUser | null => { try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); } catch { return null; } };
const readRunId = () => new URLSearchParams(window.location.search).get("run") || sessionStorage.getItem(RUN_KEY);
const readClientId = () => new URLSearchParams(window.location.search).get("client") || sessionStorage.getItem(CLIENT_KEY);
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "CA";

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<SessionUser | null>(readSession);
  const [runId, setRunId] = useState<string | null>(readRunId);
  const [clientId, setClientId] = useState<string | null>(readClientId);
  const [watching, setWatching] = useState<{ jobId: string; onDone?: (run: Run) => void } | null>(null);

  const runsQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, enabled: !!user, staleTime: 15_000 });
  const runs = useMemo(() => runsQuery.data || [], [runsQuery.data]);
  // A selected run ID is an explicit workspace decision. The server validates
  // its client boundary; do not discard it merely because the background run
  // list has not refreshed yet after a CA selects a run from the client list.
  const activeId = runId;
  const runQuery = useQuery({ queryKey: ["run", activeId, clientId], queryFn: () => api.run(activeId as string, watching?.jobId === activeId ? undefined : clientId), enabled: !!user && !!activeId, staleTime: 5_000 });
  const run = runQuery.data || null;
  const processing = run?.status === "PROCESSING";

  useEffect(() => {
    if (!watching && !processing) return;
    const jobId = watching?.jobId || (run?.run_id as string);
    const timer = setInterval(async () => {
      try {
        const job = await api.job(jobId);
        if (job.status !== "PROCESSING") {
          clearInterval(timer);
          await qc.invalidateQueries();
          const fresh = await api.run(jobId);
          // The run endpoint is the authoritative client/run boundary. Apply
          // it before rendering results, rather than waiting for a selector.
          setRunId(fresh.run_id);
          sessionStorage.setItem(RUN_KEY, fresh.run_id);
          if (fresh.client_id) { setClientId(fresh.client_id); sessionStorage.setItem(CLIENT_KEY, fresh.client_id); }
          const url = new URL(window.location.href);
          url.searchParams.set("run", fresh.run_id);
          if (fresh.client_id) url.searchParams.set("client", fresh.client_id);
          window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
          if (job.status === "COMPLETED") toast.success(fresh.rerun_count ? "Reconciliation re-run complete" : "Reconciliation complete", { description: `${fresh.summary?.result_count ?? 0} result rows · ${fresh.summary?.exceptions_count ?? 0} exceptions` });
          else toast.error("Reconciliation failed", { description: job.error || "Check the validation results and try again." });
          watching?.onDone?.(fresh);
          setWatching(null);
        }
      } catch { /* keep polling */ }
    }, 1500);
    return () => clearInterval(timer);
  }, [watching, processing, run?.run_id, qc]);

  const clearWorkspaceSelection = useCallback(() => {
    setRunId(null);
    setClientId(null);
    sessionStorage.removeItem(RUN_KEY);
    sessionStorage.removeItem(CLIENT_KEY);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    url.searchParams.delete("client");
    url.searchParams.delete("directory");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);
  // This demo session has no server-side user identity. Never carry the last
  // browser user's client/run selection into a newly authenticated session.
  const login = useCallback((name: string) => {
    clearWorkspaceSelection();
    const u = { name: name.trim() || "CA Reviewer", initials: initials(name), role: "Chartered Accountant" };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(u));
    setUser(u);
  }, [clearWorkspaceSelection]);
  const logout = useCallback(() => {
    clearWorkspaceSelection();
    sessionStorage.removeItem(SESSION_KEY);
    setUser(null);
    qc.clear();
  }, [clearWorkspaceSelection, qc]);
  const selectClient = useCallback((id: string | null) => {
    setClientId(id); setRunId(null); sessionStorage.removeItem(RUN_KEY);
    if (id) sessionStorage.setItem(CLIENT_KEY, id); else sessionStorage.removeItem(CLIENT_KEY);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("client", id); else url.searchParams.delete("client");
    url.searchParams.delete("run"); window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);
  const selectRun = useCallback((id: string | null) => {
    setRunId(id);
    if (id) sessionStorage.setItem(RUN_KEY, id); else sessionStorage.removeItem(RUN_KEY);
    const selected = runs.find((item) => item.run_id === id);
    if (selected?.client_id) { setClientId(selected.client_id); sessionStorage.setItem(CLIENT_KEY, selected.client_id); }
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("run", id); else url.searchParams.delete("run");
    if (id) url.searchParams.delete("directory");
    if (selected?.client_id) url.searchParams.set("client", selected.client_id);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [runs]);
  // Keep the completed reconciliation selected while a replacement run is
  // processing. The workspace changes only after the server returns the new,
  // terminal run, so a CA can continue to refer to the previous results.
  const watchJob = useCallback((jobId: string, onDone?: (run: Run) => void) => { qc.invalidateQueries({ queryKey: ["runs"] }); setWatching({ jobId, onDone }); }, [qc]);
  const refreshRun = useCallback(() => { qc.invalidateQueries(); }, [qc]);

  const workspaceHydrating = !!user && (runsQuery.isLoading || (!!runId && !runsQuery.isSuccess) || (!!activeId && runQuery.isLoading));
  const value = useMemo<WorkspaceValue>(() => ({ user: user || { name: "", initials: "", role: "" }, login, logout, runs, run, runId: activeId, clientId, selectClient, selectRun, workspaceHydrating, runLoading: workspaceHydrating, runError: runsQuery.isError || runQuery.isError, refreshRun, watchJob, processing: !!processing || !!watching }), [user, login, logout, runs, run, activeId, clientId, selectClient, selectRun, workspaceHydrating, runsQuery.isError, runQuery.isError, refreshRun, watchJob, processing, watching]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useWorkspace = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkspace outside provider");
  return v;
};
export const useLoggedIn = () => !!readSession();
