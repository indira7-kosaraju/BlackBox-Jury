import { useEffect, useState } from "react";
import { useApi } from "../app/session";
import { deriveCoreState, isActiveRun } from "./pipeline";
import { useQuery } from "./useQuery";

/** Watch one run (and its executions); polls only until the run is terminal. */
export function useRunWatch(runId: string | null | undefined) {
  const api = useApi();
  const [terminal, setTerminal] = useState(false);
  useEffect(() => setTerminal(false), [runId]);
  const run = useQuery(runId ? () => api.run(runId) : null, [api, runId], { pollMs: terminal ? false : 1500 });
  const active = isActiveRun(run.data);
  useEffect(() => {
    if (run.data && run.data.id === runId) setTerminal(!isActiveRun(run.data));
  }, [run.data, runId]);
  const executions = useQuery(runId ? () => api.executions(runId) : null, [api, runId, run.data?.totals.executed, run.data?.status], {
    pollMs: active ? 2000 : false,
  });
  const core = deriveCoreState(run.data, executions.data?.items);
  return { run, executions, core, active };
}
