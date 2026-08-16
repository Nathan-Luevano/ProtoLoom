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

`scripts/run_path1_cpp.sh` generates and strips an optimized C++ binary with
protoc 29.3, extracts its custom `protodesc_cold` ELF section, and verifies all
82 embedded descriptor bytes are identical. `scripts/run_path1_go.sh` builds a
stripped Go 1.24 binary with protoc-gen-go 1.36.6 and verifies all 173 embedded
descriptor bytes. Both emitted schemas recompile:

| Target | Descriptor identity | Compile rate |
|---|---:|---:|
| C++ ELF | 100.00% (82 / 82 bytes) | 100.00% (1 / 1) |
| Go ELF | 100.00% (173 / 173 bytes) | 100.00% (1 / 1) |

The generated-code descriptor deliberately omits redundant `json_name` values
that `protoc --descriptor_set_out` materializes, so identity is measured against
the actual descriptor bytes in the binary rather than a differently normalized
descriptor-set rendering of the same source schema.

## Pinned javalite matrix

`scripts/run_lite_matrix.sh` compiles the hostile local schema with protoc and
protobuf-javalite 3.21.12, 4.29.3, and 4.35.1, packages each runtime with D8
8.3.37, and also runs default and aggressive R8 legs for 4.35.1. Every archive
is SHA-256 pinned. The same generated payload exercises scalars, packed values,
nested messages, repeated messages, a map, an enum, a oneof, and proto3
optional presence.

All five legs produced the same measured result:

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 100.00% | 13 / 13 |
| Field precision | 100.00% | 13 / 13 |
| Wire-type accuracy | 100.00% | 13 / 13 |
| Type fidelity | 61.54% | 8 / 13 |
| Name recovery | 84.62% | 11 / 13 |
| Label accuracy | 100.00% | 13 / 13 |
| Structural fidelity | 50.00% | 1 / 2 |
| Enum recovery | 0.00% | 0 / 2 values |
| Compile rate | 100.00% | 1 / 1 target |
| Round-trip rate | 100.00% | 1 / 1 payload |
| Bail-out rate | 0.00% | 0 calls |

The aggressive R8 leg inlines and renames `newMessageInfo`; recovery succeeds
by recognizing the validated `RawMessageInfo` constructor shape. Message class
names are obfuscated, while this configuration leaves the field-name strings
intact. The low type-fidelity score is expected: enum fields become `int32`,
maps become repeated `bytes`, and message references lose exact qualification.

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

As a separate container-layer oracle, all 35,762 strings in Mullvad's primary
DEX matched androguard 4.x in order and value. Reproduce that check with
`uv run --with androguard python scripts/check_dex_oracle.py <apk>`.

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
