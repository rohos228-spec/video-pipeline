# Mixed media: archive recovery and preview routing follow-up

Date: 2026-09-30. Base checkpoint: `99381ee2e7c8230c9261ab9268517db3e66b9fe8`.

## Executed RED after normal Desktop startup

At `3708b2499aa2e95d555e14961c4b37cf85ab4294`, Docker Desktop was closed.
The previously authorized normal launch restored Engine 29.8.1; no configuration
or resource settings were changed. `preview-routing-red-01` ran 17 tests:
7 expected assertion failures (including subtests), no setup errors. Real HTTP
returned 404 for the project artifact and ignored explicit project scope, serving
the master copy for wrong/invalid project IDs. The 13 previous transport tests
passed. `preview-ui-red-01` failed on the exact missing `?project_id=2` URL.
Both used existing images, `--network none`, synthetic data, and zero paid calls.
The new imagePreviewLink assertions have not been reached yet; do not claim their
execution from this first URL assertion failure. Next change: scoped route/link.

The original blocked checkpoint notes below are retained as history.

With the scoped-link patch still uncommitted, `preview-ui-red-02` passed the
existing UI assertions and reached the new preview test. It failed specifically
with `TypeError: imagePreviewLink is not a function` at line 124. That is the
separate executed RED for the missing helper; its exact source snapshot is in
the results directory. The backend scoped-route GREEN run is independent.
This checkpoint contains prepared tests and documentation only. No application,
gateway, production configuration or exported UI changes. No provider requests,
credentials, dependency installation or Docker/host configuration changes.

## Recovered evidence

The retained `mixed-current-05` container results were successfully copied to
`outputs/media-v1-20260930/mixed-current-05/recovered-results` during the brief
Engine recovery. The earlier incomplete-copy boundary remains historical.

The new read-only analysis is in
`outputs/media-v1-20260930/archive-preview-followup-01/evidence.json` and
`archive-manifest.json`:

- 406 local files inventoried, including DB sidecars. Every inventoried file
  has size and SHA-256. The analysis changed none of their bytes.
- Master and project SQLite `quick_check`: `ok`. Both WAL files are zero bytes;
  analysis used SQLite `mode=ro&immutable=1` and did not import the application.
- 30 content-addressed body files: all SHA-256 values match their filenames;
  all saved JSON files parse.
- Project DB: six text nodes and `n_images` done; `n_videos` pending. One image
  artifact in project DB, none in master. One managed text-to-image transition.
- PNG: 295737 bytes, 256×384,
  `b3c695ed3e47705f4557c4489734b84a721360122d886adae6b4add24f17d09c`.
- Original report: 4684 bytes,
  `b1aefefeb126486bb1a70ee78444ddc1b53dbc113c9cafa5e8c0d543ef410cdd`.
  Two identical files are retained: `reports/shots-report.html` and the original
  node's `excel_gpt_uploads/n_excel_gpt_fw_report/shots-report.html` copy.
- Execution remains `80b56fb1-e446-4007-ac9c-d287fa6c51d5`. The worker result's
  `accepted:true` describes the completed mixed worker assertions. It does not
  cover the later browser/OOM boundary or represent complete goal acceptance.
- Exit137/OOMKilled=true remains an unresolved runtime acceptance limitation.
  Final loaded-module/shutdown proof was never written and cannot be recovered
  by copying the archive.

All 358 current `app/` source files still match the saved 19/19
`continuation-warning-green-01` source manifest. That focused result does not
replace the outstanding broader regression.

## Concrete preview routing gap

The application file route and UI do not currently agree with DB ownership:

1. `web/src/lib/media-view.ts:artifactLink` emits
   `/api/artifacts/{uuid}/file` without project scope.
2. `app/web/routers/artifacts.py:download_artifact` injects
   `app.web.deps.get_session`.
3. That dependency always opens master `SessionLocal`; it does not resolve an
   artifact's project or inspect a `project_id` query parameter.
4. The actual mixed worker persisted the artifact exclusively in project DB.
5. The earlier unit test invoked `download_artifact(..., session=s)` directly;
   its explicitly supplied session masked the HTTP dependency-selection gap.

These code and archived DB facts support a master/project routing defect.
An actual HTTP reproducer has not run yet. The prior browser
`ERR_BLOCKED_BY_CLIENT` is a separate observed result; it does not prove that
the browser reached the endpoint, and its cause is not inferred from this gap.

## Prepared regression, not an executed RED or GREEN

`tests/observe_v1/test_media_transport.py:ProjectArtifactHTTP` adds four HTTP
cases to the existing media-transport suite, using real FastAPI dependencies,
separate synthetic master/project SQLite databases and local PNG bytes:

1. Explicit project preview returns its file without copying an artifact into
   master; existing DB ownership/counts remain unchanged.
2. Wrong/unknown project must not fall back to master or another project;
   invalid scope is rejected. Legacy unscoped master-artifact access remains.
3. Scoped access still checks content hash, project containment and presence.
4. Artifact ownership must match the explicit project even if the row is
   mistakenly stored in that project's DB.

No `get_session`/`get_project_session` dependency override is used: the existing
override compatibility path aliases those resources and would hide the bug.
Only session factories point at the isolated synthetic resources. These are
endpoint tests, not a substitute for the real mixed worker acceptance.

`tests/observe_v1/frontend_contract.mjs` prepares explicit project-scoped links
and a verified-image preview contract. Unknown phase/verification, invalid
artifact identifiers and invalid project IDs must not produce preview URLs.
The referenced helper is not implemented yet.

Static checks only: Python AST and `node --check` pass. Neither test suite ran.
The `tdd-workflow` rule requires an executed, causally valid RED before changing
production code. No production patch is hidden behind this prepared-test
checkpoint; the new assertions are expected to expose the missing behavior.

## Current execution blocker

The Engine briefly answered version 29.8.1 and allowed archive collection.
`common-after-continuation-02` then failed at container create on `_ping` HTTP500;
no tests ran. The latest read-only version command also returned:

```text
500 Internal Server Error for
http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.56/version
```

The pending local version CLI was interrupted; it exited 1. No new test runner,
bridge or container was started in this follow-up. Prior owned sessions are
terminal. No Engine restart, deletion, settings change or foreign process stop
was attempted. Do not keep launching runners against the unavailable Engine.

## Resume when Engine works

Use the same worktree, existing scripts and cached images. One runner at a time.
No new environment or provider access is required.

1. Run `test_observe_offline.ps1 -Target media-transport` and
   `test_observe_frontend.ps1` with separate fresh results directories. Confirm
   the expected RED, fix any test-setup error first, then checkpoint the valid
   reproducer. Do not call an infrastructure failure RED.
2. Narrowly extend the existing file route with explicit validated project
   scope and ownership filtering, preserving legacy access and current verifier
   guards. Never scan all project DBs, accept caller file paths or fall back to
   master for a failed explicit scope.
3. Pass the current project ID from the existing panel; implement the verified
   image preview and visible load failure. Correct the shared budget caption
   which still says model POST although status/download requests also count.
4. Run the affected checks, bump UI version and build through the existing
   offline frontend script; save the verified export and checkpoint GREEN.
5. Run current `decision-final`, `runtime`, `diagnose` sequentially. Neither
   `common-after-continuation-01` nor `-02` produced test results.
6. Repeat the same media-mixed worker without a browser for clean termination
   and final loaded-file hashes. Then perform short live preview/reload checks
   and finish the browser runner promptly. Preserve the OOM evidence until a
   successful current acceptance resolves the relevant boundary.

No unrelated SSE/online reruns, long exhaustion run, new family activation or
host changes are implied. Video/audio/assembly dependencies and production
provider acceptance remain the separately documented limitations.
