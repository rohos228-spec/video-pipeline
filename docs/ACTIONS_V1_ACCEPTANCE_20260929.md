# Actions-v1: final local synthetic acceptance

Status: **ALL GREEN for the agreed Actions-v1 scope**, 2026-09-29.

Implementation checkpoint: `52b83da540e9ac957a06ba0a0ee71e2b6e6eb75f`.
Final tested harness: `d913347c168aab9f1b8de695891205d317a14d20`.
The subsequent acceptance-document commit changes no runtime, UI source or tests.

## Results

- Journal 8, Actions/API integration 24, runtime 25, diagnosis 25: **82 tests passed**.
- Managed normal, managed off, stop observation: six nodes done, five synthetic POSTs, same three frame records. Stopping observation does not stop the pipeline.
- Recovery A: nine native failures / 18 POSTs; exactly one recovery worker attempt; one committed application; verification before continuation; six nodes done; 23 POSTs total.
- Recovery B: nine native failures plus one failed recovery attempt, including its JSON retry; 20 POSTs total; human review and execution hold; no further starts.
- Five independent C copies: user_stop, uncertain request, graph revision, config revision, outside scope. Each has the intended denial reason and zero new attempts / POSTs.
- A's original exhausted checkpoint remains unchanged: 401 matching file hashes.
- Normal/A/B stay alive for four normal five-second cycles after HTML/failure. `n_script` stays pending. Normal/A also explicitly exercise the managed API scope denial. The native runtime's non-Excel manual boundary also exists; pending alone is not proof of the managed gate.
- Frontend: 38 contract assertions, TypeScript check, fresh build. No dependency/font downloads.
- Browser: off/on permission, immutable limit, scope, proposal, denial, running, verification, human review, hold/resume, close, reload, repeated click. Legacy chat and Fix bugs are rejected by managed policy.
- UI double clicks create one enable, one close, one denial; no extra recovery tickets or model events. Project NodeRuns and WorkflowRun statuses are unchanged by the UI viewers. Closing execution does not finish WorkflowRun.
- All 24 inspected acceptance/viewer containers: exit 0, network none, no mounts, no OOM. Source hashes are checked against the current worktree; browser CDP captures 12 actual JS/CSS/font assets matching the fresh export.

## Evidence

The full local report, single matrix, logs, databases, loaded-module hashes,
fresh frontend export and screenshots are preserved outside the clone:

- [Acceptance report](../../../outputs/actions-v1-resumed/acceptance-report.md)
- [Single acceptance matrix](../../../outputs/actions-v1-resumed/acceptance-matrix.json)
- [Actual Docker outcomes](../../../outputs/actions-v1-resumed/final-docker-inspect.json)
- [UI database integrity](../../../outputs/actions-v1-resumed/ui-database-validation.json)
- [External helper hashes](../../../outputs/actions-v1-resumed/external-helpers-manifest.json)

Earlier interrupted/blocked artifacts remain separate and are not counted as GREEN.
The unchanged journal result is reused only after actual source-hash and exit checks.

## Boundaries

This acceptance uses synthetic transport responses, not a real model or semantic quality assessment. UI viewers render copies of actual captured worker states with no worker/model server; the original backend API and fresh frontend are exercised. Stale diagnosis/replica disagreement is displayed, not concealed.

Recovery v1 supports the mandatory JSON/project_file path and the current native failure policy. It preserves attempts, stop flags and native failure counters. The policy's `MAX_TOTAL_FAILS = 3 * 3` is imported; this is not a new `attempts == 9` rule. Native backoff was not shortened; a three-second synthetic transport delay only made the real running phase capturable.

Master/project databases are separate; cross-database atomicity and semantic validation are not claimed. The request limit is not a monetary cap. No `.env`, real keys, paid calls, external network, push or deploy were used. No LLM decision loop, model/prompt changes or code-edit autonomy are introduced here.
