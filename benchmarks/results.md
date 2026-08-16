# Benchmark results

## Harness baseline

The first result is a deterministic local calibration target, not an extractor
accuracy claim. It deliberately contains one exact-type error, one extra field,
and one failed round trip. The published values below prove that the scoreboard
exposes each defect with a known result before extractor performance can
influence metric design.

| Metric | Macro | Micro | Less flattering |
|---|---:|---:|---:|
| Field recall | 100.00% | 100.00% | 100.00% |
| Field precision | 66.67% | 66.67% | 66.67% |
| Wire-type accuracy | 100.00% | 100.00% | 100.00% |
| Type fidelity | 50.00% | 50.00% | 50.00% |
| Name recovery rate | 100.00% | 100.00% | 100.00% |
| Label accuracy | 100.00% | 100.00% | 100.00% |
| Structural fidelity | 100.00% | 100.00% | 100.00% |
| Enum recovery | 100.00% | 100.00% | 100.00% |
| Compile rate | 100.00% | 100.00% | 100.00% |
| Round-trip rate | 0.00% | 0.00% | 0.00% |

Corpus: `tier-a-small` local calibration fixture. Targets: 1. Reproduce it with
`protoloom bench --corpus tier-a-small --per-target`. The manifest hash-pins
both inputs.

## Extractor baselines

No Path 1 corpus result has been recorded yet. CLI integration is complete and
the descriptor tests assert byte-identical recovery from noisy raw and
gzip-wrapped blobs, but a generated multi-runtime descriptor corpus has not been
run through the scoreboard. Do not compare the calibration numbers above with
extractor performance.

Likewise, the javalite field-recall, wire-type, compile-rate, round-trip, and
bail-out exit thresholds remain unmeasured. The implementation does not claim
M4's numerical exit criterion until the pinned Android build matrix is run.

## Tier B real-app run — 2026-08-16

The three hash-pinned APKs in `benchmarks/corpus/tier-b-real-apps.json` were
downloaded and verified, then processed directly without jadx. All recovered
files compiled while the command assembled the final descriptor sets.

| App | Resolved calls | Output schemas | Bail-outs | Heuristic calls |
|---|---:|---:|---:|---:|
| Molly 8.19.2-4 | 3 | 3 | 0 | 0 |
| Mullvad 2026.8 | 129 | 128 | 0 | 0 |
| Signal 8.22.2 | 168 | 167 | 0 | 166 |

Signal's visible-order array fallback is recorded at medium confidence. Its
zero bail-out count therefore does not imply high-confidence recovery.

Mullvad's pinned `management_interface.proto` provides matching ground truth.
The comparison covers 112 truth messages and 297 truth fields:

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 90.91% | 270 / 297 |
| Field precision | 90.91% | 270 / 297 |
| Wire-type accuracy | 100.00% | 270 / 270 |
| Type fidelity | 50.37% | 136 / 270 |
| Name recovery | 90.74% | 245 / 270 |
| Label accuracy | 100.00% | 270 / 270 |
| Structural fidelity | 16.36% | 9 / 55 |
| Enum recovery | 0.00% | 0 / 106 |
| Compile rate | 100.00% | 1 / 1 target |
| Round-trip rate | not measured | 0 payloads |

Reproduce the comparison with `scripts/diagnose_real_app.py` after compiling
the pinned source proto to a descriptor set. The low structural and enum scores
are real limits: the current lite model flattens nested Java classes and emits
enum fields as `int32` because verifier objects do not retain enum values.
