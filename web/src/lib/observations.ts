import { http } from "./api";
import type { ObservationEvent } from "./observation-view";

export interface ObservationScope {
  execution_id: string; project_id: number; node_keys: string[];
  observer_state: string; created_at: string;
}
interface NodeState {status: string; progress: number | null; attempts: number | null; error: string | null; progress_text: string | null}
export interface ObservationContext extends ObservationScope {
  business_group_outcome: string; current_nodes: string[];
  master: {nodes: Record<string, NodeState>}; project: {nodes: Record<string, NodeState>} | null;
  node_contexts: Record<string, {latest_attempt_id: string | null; model: string | null; checks: {kind:string; result:Record<string,unknown>}[]}>;
  disagreement: boolean; data_gaps: {kind?: string; reason?: string; error_type?: string}[];
  latest_problem: {kind: string; node_key: string; later_apply_committed: boolean; payload: {error_type?:string}} | null;
  commit_confirmation: string | null; history_window_limited: boolean; pinned: boolean;
  wait_reasons?: Record<string, {reason: string; initiator?: string}>;
}
const path = (project: number, execution?: string) => `/api/projects/${project}/observations${execution ? `/${encodeURIComponent(execution)}` : ""}`;
export const observations = {
  list: (project: number) => http<ObservationScope[]>(path(project)),
  enable: (project: number, node_keys: string[]) => http<ObservationScope>(path(project), {method:"POST",body:JSON.stringify({node_keys})}),
  context: (project: number, execution: string) => http<ObservationContext>(path(project,execution)+"/context"),
  events: (project: number, execution: string, after=0) => http<{events: ObservationEvent[]; next_cursor: number; has_more: boolean}>(path(project,execution)+`/events?after=${after}&limit=200`),
  stop: (project: number, execution: string) => http(path(project,execution)+"/stop", {method:"POST"}),
};
