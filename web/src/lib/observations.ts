import { http } from "./api";
import type { ObservationEvent } from "./observation-view";

export interface DiagnosticIncident {
  incident_id:string;node_key:string|null;node_title:string;attempt_id:string|null;
  type:string;title:string;severity:string;status:string;blocking:boolean;currently_relevant:boolean;
  fact:string;cause:{hypothesis:string|null;certainty:string};evidence_incomplete:boolean;
  request_ids:string[];event_ids:string[];evidence:{event_id:string;kind:string;db_scope?:string}[];
  recovery:{kind:string;event_id:string;accepted_response_observed:boolean}[];
  resolution:{kind:string;correlation?:string}|null;
  snapshot_evidence?:Record<string,unknown>;
  recommendation:{action:string;title:string;reason:string;automatic_action:false;safety_conditions:string[]};
}
export interface Diagnosis {
  available:boolean;lagging:boolean;evidence_complete:boolean;blocking_count:number;
  cursor:number;journal_head:number;checked_at:string|null;rule_version:string|null;
  incidents:DiagnosticIncident[];automatic_actions:false;
}

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
  diagnoses: (project:number,execution:string) => http<Diagnosis>(path(project,execution)+"/diagnoses"),
  events: (project: number, execution: string, after=0) => http<{events: ObservationEvent[]; next_cursor: number; has_more: boolean}>(path(project,execution)+`/events?after=${after}&limit=200`),
  stop: (project: number, execution: string) => http(path(project,execution)+"/stop", {method:"POST"}),
};
