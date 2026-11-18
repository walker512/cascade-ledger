# Cascade Ledger

**Turn a declared dependency design into a repeatable failure/recovery scenario test.**

Compare two topologies under the **same outage intervals, node identities, observation window and assertions**. Get a tick-by-tick explanation, per-node downtime deltas, and a portable bundle that another person can recompute.

This is an offline model-review CLI, not a production chaos injector, reliability predictor or performance simulator. No network calls, infrastructure credentials, agent integrations or scripts are executed. All supplied examples are original synthetic scenarios.

## Try it

Python 3.11+; no runtime dependencies. From this source directory:

```sh
python -m cascade_ledger compare examples/single.json examples/redundant.json --out redundancy-review --require-pass
python -m cascade_ledger verify redundancy-review --require-pass
python -m unittest discover -s tests -v
```

Use a **new output directory** for every run. Alternatively install with `python -m pip install .` and use `cascade-ledger` instead of `python -m cascade_ledger`. Installation requires setuptools as the build backend. No published package registry release is implied.

### What the example demonstrates

The model has `db-a`, `db-b`, `api` and `web`. A forced outage takes `db-a` down during ticks 1, 2 and 3. The left design requires `db-a`; the right design declares that either database suffices. Both designs require `web` to have zero down ticks and be up at the end.

| Node | Left down ticks | Right down ticks | Right minus left |
|---|---:|---:|---:|
| db-a | 3 | 3 | 0 |
| db-b | 0 | 0 | 0 |
| api | 3 | 0 | -3 |
| web | 3 | 0 | -3 |

The left assertion fails; the right passes **under these declared rules**. Database replication, consistency, capacity and failover discovery are not modeled. Simply declaring redundancy does not make it real.

`examples/grace.json` instead gives `api` four bad observations before it transitions down. In this model that suppresses a three-tick interruption. It does **not** establish that real requests would succeed during a grace period.

To see a failing gate deliberately:

```sh
python -m cascade_ledger compare examples/single.json examples/single.json --out failing-review --require-pass
python -m cascade_ledger verify failing-review --require-pass
```

Both commands exit **3**; the resulting bundle is internally consistent and its right-hand model fails the requested assertion. This is different from invalid input or corrupt evidence.

## Files to review

- `left.json`, `right.json`: complete original input bytes, not a redacted subset.
- `report.json`: both traces, summary, explicit assertions, changed node declarations and input hashes.
- `timeline.csv`: every side/node/tick with up/down state, direct transition reason, failed dependency group IDs and consecutive-good/bad counters. No spreadsheet formulas are emitted: all IDs must begin with an ASCII letter.
- `review.md`: compact summary with model limitations, suitable for code review.

`verify` recomputes every output byte from the bundled inputs. It detects inconsistent changes, not coordinated replacement of inputs and outputs. It authenticates neither authorship nor the physical behavior of a system. Keep an independently trusted hash/signature if provenance matters.

## Input contract

Every object uses exactly the documented fields; unknown fields are rejected.

```json
{
  "schema": 1,
  "ticks": 10,
  "nodes": [
    {"id": "database", "fail_after": 1, "recover_after": 2, "groups": []},
    {"id": "api", "fail_after": 2, "recover_after": 1,
     "groups": [{"id": "storage", "providers": ["database"], "minimum": 1}]}
  ],
  "outages": [{"node": "database", "start": 1, "end": 4}],
  "assertions": [{"node": "api", "max_down_ticks": 3, "up_at_end": true}]
}
```

A node needs **all** its groups satisfied. Within a group, at least `minimum` distinct providers must be up. Provider references must name other nodes. Cycles are permitted, self-references are not. Empty groups mean the node has no modeled dependencies, not that it has none in reality.

`up_at_end: false` means “do not require recovery at the last tick,” **not** “require the node to be down.” Maximum down ticks always applies. Empty assertions yield `no_assertions`, never `pass`.

### Exact time semantics

Ticks are abstract integer steps, not measured seconds. A row at tick `t` records the new state after that tick's update.

1. Before tick 0, all nodes are up and both streak counters are zero. No prehistory, warm-up or initial damaged state is inferred.
2. All nodes read provider states from **the previous tick**. Updates are simultaneous, not dependent on input ordering. A forced failure at tick 0 can therefore affect a direct dependent no earlier than tick 1.
3. A forced outage is active on `[start, end)`. It immediately sets its own node down and resets both streak counters, regardless of grace or recovery settings.
4. Otherwise, an unsatisfied group increments the consecutive-bad count and resets the good count. An up node becomes down when bad count reaches `fail_after`. An already-down node remains down while any group is unsatisfied.
5. Otherwise, bad count resets. An up node stays up with good count reset to zero. A down node increments the good count and recovers when it reaches `recover_after`. A new interruption resets that recovery wait.
6. Down ticks count rows marked down. The last observed state is reported without predicting what happens after the window.

`failed_groups` always describes the previous-tick dependency observation, including during forced outages. `cause` is the immediate model rule used, **not** a uniquely inferred real-world root cause. `recovered` identifies the threshold-crossing tick. Cyclic graphs may latch down or exhibit alternating behavior; the tool does not force convergence or claim eventual recovery.

## Comparing designs fairly

Both inputs must have the same node IDs, ticks, outage intervals and assertions. Order of nodes, outages and assertions need not match. Dependencies and timer declarations may differ. Changing an outage or weakening an assertion is rejected rather than reported as an architectural improvement.

This guard is limited: a designer can remove a real dependency from the model and produce a better-looking result. Inspect `changed_nodes` and the complete inputs. A lower modeled down-tick count is not evidence that a proposed architecture is safe, deployable, cheaper or faster. Equal totals can also hide different timelines.

Exit codes:

- **0**: comparison written or bundle recomputation matched. Without `--require-pass`, this does not require any model assertion to pass.
- **2**: invalid input/comparison, inconsistent bundle, input/output size limit, filesystem or argument error.
- **3** with `--require-pass`: the **right-hand** gate failed or has no assertions. The left is an informational baseline and may fail.

## Bounds and privacy

Up to 50 nodes, 200 ticks, 10 groups per node, 500 total provider references, 100 non-overlapping outage intervals, and 50 unique-node assertions. IDs are 1–48 ASCII letters/digits/underscore/dot/hyphen and must start with a letter. All numeric fields require bounded integers, not booleans or floats. Inputs are capped at 1 MiB; duplicate keys, excessive depth, malformed Unicode and non-finite numbers are rejected. Reports have a separate 24 MiB per-file cap and are serialized and size-checked **before** the destination is created.

Files must be ordinary non-symlink files. Leaf symlinks and special files are rejected; this is not an OS sandbox against a hostile filesystem administrator or malicious parent directory replacement. No existing output directory is overwritten. An I/O failure after creation can leave a partial bundle, which must not be treated as complete. `verify` rejects extra/missing files and recomputes derived files.

No telemetry, network fetch, secret scan or automatic redaction. Topology names may be sensitive. Inspect both complete models before sharing. Error messages avoid echoing supplied contents. Do not put credentials into a model. Please do not include secrets in public issue reports; no private vulnerability-reporting address is designated by this release.

## What this does not model

No traffic, request success probabilities, load/capacity, queues, retry storms, timeouts per request, network partitions, data corruption, backup correctness, probabilistic failures, cost, latency or real deployment execution. There is no fitted calibration against production observations. The simulator assumes binary states and exact user-specified timers. Results are a reviewable consequence of those assumptions, not an SLO forecast.

This is separate from a static component-compatibility checker: it evolves state and streaks over time, checks finite-window assertions and compares two declared dynamic scenarios.

## Related projects and comparison boundary

- [breakscale](https://github.com/workersio/breakscale) provides a broader interactive system-design simulator, including load and chaos controls. Prefer it when richer interactive behavior is the task.
- [MiSim](https://github.com/Cambio-Project/MiSim) is a microservice resilience simulator built around discrete-event modeling.
- [percolate](https://github.com/haru-g-cmd/percolate) describes graph-based cascading-failure simulation and real-time visualization.
- [Chaos Toolkit](https://chaostoolkit.org/reference/api/experiment/) defines experiments with hypotheses, actions and probes against systems; this tool runs no such actions.

Their primary documentation was inspected, **not** their runtimes. No benchmark, feature-exclusivity, novelty or superiority claim follows. Cascade Ledger's concrete scope is a ready-to-run, bounded, deterministic, same-scenario CLI review bundle with explicit model semantics and optional assertion gates. The same computations could be implemented with other simulation tooling or a custom script.

## Validation scope

33 public tests run on Linux/Python 3.12: hand-derived chain propagation, quorum and conjunctive groups, grace and recovery streak resets, cycle latching, ordering, finite horizon, 50 seeded independent chain-oracle checks, input boundaries, same-challenge checks, gate exit codes, exact bytes, tampering, bounded file handling and maximum 50-node/200-tick/500-reference bundle self-verification.

Windows/macOS and fresh hosted CI have not been run. Source uses explicit UTF-8/LF bytes for generated outputs, but that is not a claim of executed Windows validation. Symlink/FIFO tests can skip where the OS does not support them; skips are not passes. No browser UI or browser test is part of this CLI. Synthetic tests do not establish user adoption or production accuracy.

MIT license. Original code and examples, copyright walker512. No upstream competitor code or user incident data is bundled.

## Request-level retries, timeout effects and admission policies

The optional [request-policy rehearsal](docs/REQUEST_POLICIES.md) layers a declared workload on the same dependency-health model. Compare the same latency/duplicate/outage challenge under different timeouts, retry backoff, fixed per-tick admission limits, cooldown breakers and an explicit idealized deduplication assumption. Timed-out backend work is not silently canceled; late executions and unfinished work remain visible.

Use `request-compare` and `request-verify` for this separate four-file bundle. The original `compare`/`verify` and their model semantics are unchanged. This adds deterministic scenario calculations, not production fault injection, measured capacity, an interactive graph UI or proof of real idempotency. See the complete event-order rules and synthetic example before applying conclusions.

This increment's public suite has 51 tests (the original 33 unchanged plus 18 request tests), run locally on Linux/Python 3.12. The arithmetic-oracle combinations are cases inside one test, not additional separately reported test methods. No new cross-platform or GUI acceptance is claimed.

## Private output publication correction (candidate)

Both `compare` and `request-compare` now reserve a new directory with requested
Unix mode **0700**, and create each bundle file exclusively with requested mode
**0600 before writing any content**. No process-wide umask change is made. Existing
files, empty directories, nonempty directories and dangling symlinks are refused,
not overwritten or chmodded. This matters because bundles include complete
architecture inputs, failure plans and derived evidence, not a redacted summary.

Use a trusted existing parent directory and a private local account. These modes
are not encryption, secure erasure, protection against root/same-account malicious
path replacement, or a guarantee against filesystem ACL overrides. Windows ACL
isolation is not established by POSIX mode arguments; use operating-system access
controls. Previously generated public-permission bundles are not automatically
repaired and past disclosure cannot be undone.

Input validation and computation finish before directory reservation. Publication
of multiple files is **not atomic**: interruption or I/O failure can leave an
incomplete private directory. `verify`/`request-verify` reject incomplete bundles;
preserve or manually remove the partial output as appropriate and retry with a
new directory. No automatic cleanup, repair or durability guarantee is claimed.

The candidate passed **60 tests, zero skips** on Linux Python 3.12 (51 existing
plus 9 publication regressions), including both bundle modes under umask 000/022,
inspection before the first byte, 24-thread competition, eight real CLI processes,
symlink/file collisions and interrupted-copy rejection. Compileall also passed.
This is CLI/filesystem evidence only; no browser action was attempted and no new
Windows/macOS, remote CI, production system or visual acceptance is claimed.
The broader original interactive-simulation project remains incomplete.
