# Decision-v1 acceptance — 2026-09-29

GREEN: 115 Python (Decision 33, Actions 24, runtime 25, journal 8, diagnosis 25), 50 frontend assertions, TypeScript, fresh build. New module aggregate coverage: state 98%, transport 83.3%, decision 95.8%.

Default-off persisted bounded synthetic model loop uses original gpt_api, scoped tools, strict evidence/context policy and existing Actions executor. One owner, max 3 turns, durable shared request budget. Body/SSE audit gap blocks send. Late/stale/stop/uncertain proposals rejected. Restart adopts persisted action and never repeats it. No production provider support.

Evidence: outputs/decision-v1/acceptance-matrix.json, container-isolation.json, ui-database-validation.json.
Worker success: worker-audited/artifacts, 3 brain + 5 worker synthetic POST, ledger 18→26, one recovery, original planner completes all six nodes and HTML, n_script pending. Tool and worker results included in subsequent model turns.
Worker failure: worker-bad-audited/artifacts, 2 brain + 2 worker POST, ledger 18→22, one recovery, failed verify→human review/hold, no further requests.
Regression: regression-managed-audited and regression-off-audited both six done/five POST.
UI: ui-verified and ui-human screenshots/validation; disable persists after reload. Actual loaded frontend 12 assets match fresh 43-file export; viewer backend 156/154 module SHA-256 match. Worker acceptance actual modules match current code. Run-config source_commit a426 identifies pre-change base; actual loaded hash manifests bind implementation, final checkpoint stored separately.

27 historical/accepted containers inspected: network none, no mounts, OOM false, stopped. Failed RED and harness cases retained, not relabeled successful. Genuine native exhausted seed copied, original unchanged.

Human viewer original GET /projects/{id}/run synchronized project WorkflowRun edges_snapshot with project.meta.canvas_graph (25→24, removed n_plan→n_script) and updated_at. Confirmed projects.get_project_run→canvas_graph.sync_run_snapshot_from_canvas_graph. All other WorkflowRun columns, node attempts/statuses, frames/scenes unchanged. Before/after DBs preserved. Viewer runs no worker/model/observer loop; this API projection behavior is not supervisor execution.

External helpers/results: outputs/decision-v1/prepare_viewer.py, viewer_control.py, consolidate.py, external-tools-manifest.json; reused original local_browser_bridge.mjs for loopback UI relay, no Docker socket in container. Full redacted bodies retained locally separately by hash/reference; not published.

Limitations: scripted fixture reasoning only; no real LLM-quality assertion. Historical incidents retained, group done != entire WorkflowRun done. UI snapshot replay has observer loop disabled, build badge unknown without .git; actual loaded asset hashes verified. No profiles, arbitrary prompt/code edit, or data rollback implemented at this checkpoint.
Real APIs 0; publications 0; remote mutations 0.
