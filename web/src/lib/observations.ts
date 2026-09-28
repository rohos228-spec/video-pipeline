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
  observer_state: string; created_at: string; graph_version?: string;
}
export interface ActionRecord {
  action_id:string;action_kind:string;node_key:string|null;attempt_id:string|null;
  initiator:string;rationale:string;preconditions:Record<string,unknown>;status:string;
  verification:Record<string,unknown>;created_at:string;
}
export interface ActionState {
  configured:boolean;enabled:boolean;held:boolean;group_id:string|null;node_keys:string[];
  graph_version:string|null;request_limit:number|null;requests_used:number;records:ActionRecord[];
  recoveries:{action_id:string;incident_id:string;node_key:string;source_attempt_id:string;new_attempt_id:string;
    status:string;preconditions:Record<string,unknown>;verification_result:Record<string,unknown>}[];
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
  actions: (project:number,execution:string) => http<ActionState>(path(project,execution)+"/actions"),
  enableActions: (project:number,execution:string,payload:{group_id:string;node_keys:string[];rationale:string;request_limit:number}) =>
    http(path(project,execution)+"/actions/enable",{method:"POST",body:JSON.stringify(payload)}),
  controlActions: (project:number,execution:string,held:boolean,rationale:string,idempotency_key:string) =>
    http(path(project,execution)+"/actions/control",{method:"POST",body:JSON.stringify({held,rationale,idempotency_key})}),
  closeActions: (project:number,execution:string,rationale:string,idempotency_key:string) =>
    http(path(project,execution)+"/actions/close",{method:"POST",body:JSON.stringify({rationale,idempotency_key})}),
  startManagedNode: (project:number,execution:string,payload:{node_key:string;step_code:string;rationale:string;idempotency_key:string}) =>
    http<{action_id:string;status:string;idempotent_replay:boolean}>(path(project,execution)+"/actions/start",{method:"POST",body:JSON.stringify(payload)}),
  verifyNode: (project:number,execution:string,payload:{node_key:string;rationale:string;idempotency_key:string}) =>
    http<{action_id:string;status:string;verification:Record<string,unknown>}>(path(project,execution)+"/actions/verify",{method:"POST",body:JSON.stringify(payload)}),
  reviewIncident: (project:number,execution:string,payload:{incident_id:string;rationale:string;idempotency_key:string}) =>
    http<{action_id:string;status:string;business_action:boolean}>(path(project,execution)+"/actions/review",{method:"POST",body:JSON.stringify(payload)}),
  retryNode: (project:number,execution:string,payload:{incident_id:string;node_key:string;rationale:string;idempotency_key:string}) =>
    http(path(project,execution)+"/actions/retry",{method:"POST",body:JSON.stringify(payload)}),
};
