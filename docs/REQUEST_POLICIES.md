# Request-policy rehearsal

This extension adds a finite, deterministic **request-level simulation** on top of Cascade Ledger's existing declared node-health timeline. It sends no real requests, sleeps for no real delay, injects nothing into a running system and executes no supplied code. It is not a measured performance or reliability model. It does not implement an interactive graph editor.

## Runnable synthetic comparison

```sh
python -m cascade_ledger request-compare examples/request-timeouts.json examples/request-adjusted.json --out request-review --require-pass
python -m cascade_ledger request-verify request-review --require-pass
```

Both variants receive the same one logical write at tick 0, service latency 4 and duplicate-delivery multiplicity 2, with the same healthy topology and assertions. Left: timeout 2, one retry after 1 tick, no deduplication. Its attempts at 0 and 3 time out at 2 and 5, but complete in the backend at 4 and 7: **one failed logical request, four modeled executions**. Right: timeout 4 and idealized deduplication. Completion at tick 4 wins the equal deadline: **one successful logical request, one execution**. These are exact consequences of declared rules, not proof that changing a timeout or adding a flag fixes a real service. The deduplication implementation, keys, persistence and atomicity are not tested.

`report.json` lists all request states, admitted attempts, admission rejections, retries, breaker openings, timeout events and backend completions. `review.md` summarizes differences. Complete inputs are included, not redacted. All IDs are restricted ASCII identifiers; no free-text private payloads are needed.

## Contract

Each input has exactly `schema: 1`, `topology`, `requests`, `policy`, `assertions`. `topology` is the original Cascade Ledger schema with all its existing limits and synchronous previous-tick dependency semantics. Its forced outages and propagated health feed this model; no other capacity or real network state is inferred.

Each of 1–100 requests contains:

- `id`: unique identifier; `target`: an existing topology node.
- `tick`: initial arrival, in the observation window.
- `latency`: fixed integer 1–200, in abstract ticks, from admission to backend completion. This is a supplied scenario value, not inferred from load.
- `copies`: 1–3 modeled duplicate executions **per successful admitted attempt**, before optional idealized deduplication. Copies share the same outcome and latency. They are not independently scheduled network packets or separately rate-limited starts.

One policy is shared by all nodes, with independent per-node admission counts/breaker states:

- `timeout`: 1–200 ticks from admission; `retries`: 0–5 additional admission attempts; `backoff`: 1–200 ticks after an observed failure or rejection.
- `start_limit`: 1–100 admitted attempts per node per tick. This is a fixed-window admission limit, **not** a concurrency limit, token bucket, queue or CPU capacity model. Duplicates do not consume extra admissions.
- `breaker_failures`: 0 disables the breaker, otherwise 1–100 consecutive observed backend failures/timeouts opens it. `breaker_open_ticks`: 1–200.
- `deduplicate`: exact boolean. `true` assumes perfect durable, request-ID-level deduplication across duplicate copies and retries. The first successful backend completion counts once, all subsequent successes for that logical ID count zero. This is an explicit **assumption**, not a feature of any real service.

Request assertions are `max_failed` and `max_pending` (0–100), and `max_executions` (0–1800), all required. The overall gate passes only if **both request assertions and original topology assertions pass**. No original topology assertions means `no_assertions`, even if requests meet their limits. `--require-pass` checks the right overall gate and exits 3 for failure/no assertions. Exit 0 without that flag only means a comparison/verification succeeded; exit 2 means invalid input, inconsistent evidence or I/O error.

## Exact per-tick order

1. Reset a node's observed failure streak when its current breaker window expires. Initial streaks are zero and breakers closed. There is no imported prehistory.
2. Process due backend completions in admission order (admission tick, then lexical request ID). Backend success requires its target to be healthy at **every tick from admission through completion, inclusive**. Any down tick makes the entire attempt fail; partial commits are not modeled. Successful backend executions are counted even when the client has already timed out or exhausted retries.
3. A completion whose response is still awaited delivers success/failure. Success finishes the logical request and resets that node's observed failure streak. It does not end an already-open breaker window early. Failure may schedule a retry. Completions of timed-out attempts are marked `late` and never resurrect the client result or alter its breaker streak.
4. Process due client deadlines on remaining awaited responses. If completion and deadline tie, completion already won in step 2. A timeout changes the client response but **does not cancel the backend**. It increments the observed failure streak and can schedule a retry.
5. Dispatch due arrivals/retries in lexical request-ID order. An open breaker rejects first; otherwise exceeding the node's start limit rejects. There is no queue. Every rejection consumes one allowed admission attempt and may retry after the configured backoff; rejection itself does not increase the breaker failure streak. Admission starts the backend, its completion time and its client deadline. Starts at this tick cannot complete until a later tick because latency is positive.

After an observed failure/timeout reaches the breaker threshold, the breaker is open for `[failure_tick, failure_tick + breaker_open_ticks)`, so new dispatches later in that same tick are rejected. Additional in-flight observed failures may extend the window. This is a simplified **cooldown breaker**, with no half-open probe state. Successful in-flight responses reset the failure count but do not shorten an open window. For multiple observations at the same tick, the declared completion-before-timeout ordering is intentional, not nondeterministic scheduling.

A retry is scheduled at `failure_or_rejection_tick + backoff`; backoff is strictly positive. Once all initial-plus-retry attempts are consumed, the logical request is failed. Request success or failure does not erase any already-running backend operation. The model therefore exposes duplicate side effects rather than pretending a timeout rolled them back.

The observation window is `[0, topology.ticks)`. A completion, deadline or retry exactly at the window end is not processed. Such logical work stays pending unless it has already reached a terminal state; outstanding backend work is counted separately as `backend_still_running`. No eventual success, execution count or cancellation is guessed after the window. An assertion allowing pending work is a conscious weakening of that scenario, not evidence it will succeed.

## Fair comparison and limits

The same original node IDs, outage intervals, observation window and topology assertions are required on both sides, using the original comparison guard. Request IDs/targets/arrival ticks/latencies/copies and request assertions must also match, regardless of input order. Policies and declared dependency/timer structure may differ; changes are listed. The baseline may fail while the right variant passes. Removing a real dependency from a model or assuming perfect deduplication can improve its result without improving reality: inspect complete inputs.

Input files are bounded by the existing 1 MiB parser, strict keys/types, depth and special-file rules. Generated outputs are checked against 24 MiB before creating a new destination; existing output is never overwritten. Final multi-file writes are not an atomic transaction and an I/O failure can leave a partial directory. Bundle verification requires the exact four-file set and recomputes bytes. It cannot authenticate coordinated replacement of inputs and outputs or guarantee that a supplied model represents a real system.

Public tests include a closed-form, single-request arithmetic oracle over 768 timeout/latency/window/retry/backoff/deduplication combinations, plus outage recovery, deadline ties, per-node rate admission, breaker cooldown, topology gates, stale/tampered bundles and actual CLI compare/verify. Tests are synthetic, not measurements from a production service. No new browser, Windows/macOS or real fault-injection run is claimed.
