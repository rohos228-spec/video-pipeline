
export type ProfileOverlay = {overlay_id:string;node_key:string;attempt_id:string|null;profile_id:string;base_version:string;
  candidate_hash:string;candidate:{model_id:string};initiator:string;rationale:string;status:string;history:unknown[];
  recovery?:{status:string;verification:{verified?:boolean}}|null};
export type ProfileState = {base_version:string|null;catalog_hash:string;synthetic_only:boolean;available:boolean;reason:string|null;
  catalog:{profile_id:string;model_id:string;reasoning_effort?:string;max_output_tokens?:number;prompt_variant?:string}[];overlays:ProfileOverlay[]};
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
export interface DecisionState {
  enabled:boolean;synthetic_only:boolean;max_model_steps:number;
  runtime?:{mode:string;configured:boolean;reason:string|null;allowed_actions:string[];
    decision_model:{model_id:string;reasoning_effort:string;max_output_tokens:number}|null};
  records:{decision_id:string;incident_id:string;node_key:string;status:string;model_steps:number;
    action_id:string|null;context_version:string;proposal:{action?:string;explanation?:string;evidence_ids?:string[]};
    policy_result:{allowed?:boolean;reason?:string;wait?:{until?:string}};verification:{verified?:boolean};
    turns:{id:number;role:string;payload:Record<string,unknown>}[]}[];
}
interface NodeState {status: string; progress: number | null; attempts: number | null; error: string | null; progress_text: string | null}
export interface MediaContext {
  result_kind:string; limited_management:boolean; synthetic_only:boolean;
  production_profile_configured:boolean; blockers:string[];
  allowed_actions:string[];
  publication?:{dry_run:boolean;send_allowed:boolean;requires_approval:boolean;
    caption_characters:number;platforms:{platform:string;already_recorded:boolean}[];
    validation:{status:string;reason?:string}};
  requests:Record<string,number>;
  jobs:{job_id:string;provider_job_id:string|null;phase:string;model_id:string;
    progress:number|null;error:string|null;attempt_id:string;
    verification:{status?:string;reason?:string;bytes?:number;width?:number;height?:number;duration?:number};
    artifact_uuid:string|null}[];
}
export interface ManagedLaunch {
  launch_id:string;execution_id:string;workflow_run_id:number;entry_node:string;
  status:string;idempotent_replay?:boolean;result:Record<string,unknown>;
  policy:{node_keys:string[];request_limit:number;allowed_actions:string[];profile_ids?:string[]};
}
export interface ManagedLaunchOptions {
  workflow_run_id:number;graph_version:string;configured:boolean;mode:string;reason:string|null;
  online_authorized:boolean;request_limit_default:number;request_limit_max:number;strategies:string[];
  profile_catalog_hash:string|null;profiles:ProfileState['catalog'];
  nodes:{node_key:string;title:string;group_id:string|null;supported:boolean;status:string;reason:string|null;result_kind:string;actions?:string[]}[];
  latest_launch:ManagedLaunch|null;
}
export interface ManagedLaunchRequest {
  workflow_run_id:number;graph_version:string;node_keys:string[];strategies:string[];
  profile_ids?:string[];profile_catalog_hash?:string|null;
  request_limit:number;rationale:string;idempotency_key:string;
}
export interface TelemetrySummary {
  project_id:number; since:string|null;
  incidents:{total:number;by_type:Record<string,number>;by_status:Record<string,number>;
    by_severity:Record<string,number>;resolved_confirmed:number;blocking_open:number};
  decisions:{total:number;by_status:Record<string,number>;terminal_count:number;
    human_review_rate:number|null;avg_model_steps:number|null;max_model_steps:number|null};
  node_latency:{by_node:Record<string,{count:number;avg_seconds:number;min_seconds:number;max_seconds:number}>;
    unmatched_requests:{node_key:string|null;request_id:string;started_at:string}[]};
  budgets_by_execution:Record<string,{request_limit:number|null;requests_used:number|null;
    remaining:number|null;utilization:number|null;held:boolean|null;enabled:boolean|null}>;
  cost:{total_calls:number;failed_calls:number;total_cost_usd:number;
    total_prompt_tokens:number;total_completion_tokens:number;total_cached_tokens:number;
    by_model:Record<string,{calls:number;cost_usd:number;prompt_tokens:number;completion_tokens:number}>;
    source:string};
}
export interface ObservationContext extends ObservationScope {
  business_group_outcome: string; current_nodes: string[];
  master: {nodes: Record<string, NodeState>}; project: {nodes: Record<string, NodeState>} | null;
  node_contexts: Record<string, {latest_attempt_id: string | null; model: string | null; checks: {kind:string; result:Record<string,unknown>}[]; media?:MediaContext}>;
  disagreement: boolean; data_gaps: {kind?: string; reason?: string; error_type?: string}[];
  latest_problem: {kind: string; node_key: string; later_apply_committed: boolean; payload: {error_type?:string}} | null;
  commit_confirmation: string | null; history_window_limited: boolean; pinned: boolean;
  wait_reasons?: Record<string, {reason: string; initiator?: string}>;
}
const path = (project: number, execution?: string) => `/api/projects/${project}/observations${execution ? `/${encodeURIComponent(execution)}` : ""}`;
export const observations = {
  launchOptions:(project:number)=>http<ManagedLaunchOptions>(path(project)+'/launch-options'),
  launch:(project:number,payload:ManagedLaunchRequest)=>http<ManagedLaunch>(path(project)+'/launch',
    {method:'POST',body:JSON.stringify(payload)}),
  decisions:(project:number,execution:string)=>http<DecisionState>(path(project,execution)+'/decisions'),
  controlDecisions:(project:number,execution:string,enabled:boolean,rationale:string)=>
    http(path(project,execution)+'/decisions/control',{method:'POST',body:JSON.stringify({enabled,rationale})}),
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
  profiles: (project:number,execution:string,node:string)=>http<ProfileState>(path(project,execution)+"/profiles?node_key="+encodeURIComponent(node)),
  approveProfile: (project:number,execution:string,payload:{node_key:string;profile_id:string;base_version:string;catalog_hash:string;rationale:string})=>
    http(path(project,execution)+"/profiles/approve",{method:"POST",body:JSON.stringify(payload)}),
  cancelProfile: (project:number,execution:string,overlay:string,rationale:string)=>
    http(path(project,execution)+"/profiles/"+encodeURIComponent(overlay)+"/cancel",{method:"POST",body:JSON.stringify({rationale})}),
  retryNode: (project:number,execution:string,payload:{incident_id:string;node_key:string;rationale:string;idempotency_key:string}) =>
    http(path(project,execution)+"/actions/retry",{method:"POST",body:JSON.stringify(payload)}),
  // Project-wide, deliberately not execution-scoped -- aggregates across every execution this project has had.
  telemetry: (project:number,since_hours?:number)=>http<TelemetrySummary>(path(project)+"/telemetry"+(since_hours?`?since_hours=${since_hours}`:"")),
};
