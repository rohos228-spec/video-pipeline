# Actual browser acceptance: initial reconnect

Use the existing synthetic history viewer, not a new pipeline run.
Block only the initial project observations GET in the test browser, reload,
observe the unavailable-history message, then remove the browser block.
Without reloading again, the panel must restore the same persisted execution
and unique event IDs. No POST and no new scope may be created.

RED captured on 2026-09-28: after removing the block, the initial load never
retried. Evidence: outputs/observe-v1/20260928-ui-browser-02/artifacts/
initial-reconnect-red.json (relative to workspace root).

The browser block is a temporary development fault injection and must always
be removed. It changes no host/container network policy.
