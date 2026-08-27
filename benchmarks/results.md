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

## Tier A pinned upstream corpus

Tier A now includes 13 compiled schema roots from four commit-pinned upstream
families rather than treating the one local calibration fixture as corpus
coverage. The roots cover protobuf `unittest_proto3`, well-known-type unittest,
test messages, and conformance; five `google/type` schemas; gRPC Health; and
three self-contained Envoy schemas. Together they contain 80 messages, 549
fields, 64 enum values, and 32,397 serialized root-descriptor bytes.

| Measurement | Result | Count |
|---|---:|---:|
| Root descriptor recovery | 100.00% | 13 / 13 |
| Descriptor byte identity | 100.00% | 32,397 / 32,397 |
| Recovered proto compile rate | 100.00% | 13 / 13 |
| Field recall and precision | 100.00% | 549 / 549 |
| Wire and exact type fidelity | 100.00% | 549 / 549 |
| Enum recovery | 100.00% | 64 / 64 values |
| Generated C++ object recovery | 0.00% | 0 / 3 objects |
| Round trip | not measured | 0 payloads |

The perfect descriptor scores are Path-1 results, not a claim that stripped
generated code is solved: each selected root descriptor is embedded directly
for the exact scanner oracle. As a separate compiled-code check, the driver ran
protoc's C++ generator and compiled optimized objects for protobuf conformance,
Google Date, and gRPC Health. None exposed a standalone serialized descriptor
accepted by the current scanner. That 0/3 result is retained in
`measurements.json`; the existing custom-section C++ and Go carriers remain the
positive compiled Path-1 cases below. No payloads belong to this corpus, so the
benchmark renderer's vacuous 100% for a zero round-trip denominator is not used
as a result.

Reproduce both generation and scoring with `scripts/run_tier_a_upstream.py`
followed by `protoloom bench --corpus tier-a-upstream --per-target`. The source
manifest pins full commits, archive or file hashes, and byte sizes. The checked
in benchmark manifest separately hash-pins every generated truth and recovery
input.

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

All five legs share the metrics in the first table. Optimizer-sensitive type
and enum fidelity are shown per leg in the second table. Bail-outs differ because
the D8 archives include the complete javalite runtime, and every unresolved or
opt-in heuristic call is now counted instead of disappearing behind a
medium-confidence finding:

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 100.00% | 13 / 13 |
| Field precision | 100.00% | 13 / 13 |
| Wire-type accuracy | 100.00% | 13 / 13 |
| Name recovery | 84.62% | 11 / 13 |
| Label accuracy | 100.00% | 13 / 13 |
| Structural fidelity | 50.00% | 1 / 2 |
| Compile rate | 100.00% | 1 / 1 target |
| Round-trip rate | 100.00% | 1 / 1 payload |
| Type-fidelity ceiling | 92.31% | 12 / 13 fields |
| Counted uncertainty | see below | every unresolved or heuristic call |

| Leg | Counted calls | Type fidelity | Enum recovery |
|---|---:|---:|---:|
| javalite 3.21.12 + D8 | 27 | 76.92% (10 / 13) | 100.00% (2 / 2) |
| javalite 4.29.3 + D8 | 61 | 76.92% (10 / 13) | 100.00% (2 / 2) |
| javalite 4.35.1 + D8 | 61 | 76.92% (10 / 13) | 100.00% (2 / 2) |
| javalite 4.35.1 + default R8 | 2 | 69.23% (9 / 13) | 100.00% (2 / 2) |
| javalite 4.35.1 + aggressive R8 | 2 | 61.54% (8 / 13) | 0.00% (0 / 2) |

Heuristic lite recovery is disabled by default. The matrix opts in with
`--allow-heuristic-lite` to measure the recoverable floor, and the report still
counts each emitted heuristic call. Without the flag those calls bail out and
emit no guessed schema.

The aggressive R8 leg inlines and renames `newMessageInfo`; recovery succeeds
by recognizing the validated `RawMessageInfo` constructor shape. Message class
names are obfuscated, while this configuration leaves the field-name strings
intact. Default and aggressive R8 therefore have the same 84.62% field-name
score: this matrix did not demonstrate a field-name cliff. The result measures
call-shape and class-name damage, not loss of metadata field-name strings.
Unobfuscated getter return types now identify the enum class, and its static
initializer proves both value names and numbers through constructor arguments
and matching static-field stores. That moves enum recovery from 0/2 to 2/2 on
all D8 legs and default R8. Aggressive R8 renames the getter and removes that
field-to-enum association, so it honestly remains 0/2. D8 also retains the
`MapEntryLite.newDefaultInstance` call and exact `WireFormat.FieldType` static
fields, recovering `map<string, int32>`. Qualified enum scoring brings the D8
legs to 10/13 and default R8 to 9/13. Both R8 modes reshape the map initializer
beyond this exact proof, and aggressive R8 also lacks the enum association, so
it remains 8/13. Original nesting is not invented when evidence is absent;
structural fidelity remains 1/2 on this fixture.
The ceiling applies the roadmap's declared ambiguity between the
`int32`/`sint32`/`uint32` and `int64`/`sint64`/`uint64` families. The remaining
23.08-point D8 gap and 30.77-point R8 gap are implementation loss, chiefly
message-reference and optimizer-sensitive map reconstruction; neither is
presented as an information limit.

## Tier B real-app runs

The eight hash-pinned APKs in `benchmarks/corpus/tier-b-real-apps.json` were
downloaded, verified, and rerun directly without jadx under the current strict
default. Signal and Molly remain a deliberate fork pair and count as one schema
family, giving seven independent families across eight shipping artifacts.
Bitwarden and Mullvad were rerun on 2026-08-17 after the nested-enum recovery
change; their rows below contain those newer results.

| App | Output schemas | Bail-outs | Strict-default result |
|---|---:|---:|---|
| Signal 8.22.2 | 9 | 404 | unresolved-order guesses refused |
| Molly 8.19.2-4 | 3 | 206 | partial recovery |
| Mullvad 2026.8 | 128 | 0 | recovered |
| Bitwarden Authenticator 2026.7.1 | 104 | 0 | recovered |
| Meshtastic 2.8.1-internal.3 | 52 | 1 | recovered; separately pinned schema repository |
| Flipper 1.8.1.1890 | 0 | 6 | no recoverable evidence |
| Gadgetbridge 0.93.0 | 20 | 671 | partial recovery |
| Smartspacer 1.11.2 | 70 | 2 | recovered with explicit uncertainty |

An opt-in rerun increases Signal output from 9 to 183 schemas while still
reporting 404 other bail-outs. That run is evidence of the recoverable floor,
not strict behavior; the table above is the current default and refuses the
visible-order guesses.

These are coverage smoke results, not accuracy figures. Ground-truth scoring
requires matching recovered messages to each selected source schema and is not
silently inferred from unrelated dependency protos found in an APK.

### Expanded-app ground-truth diffs

The exact source commits from the manifest were checked out separately and the
selected files were compiled with protoc 29.3, including their transitive
imports. Scores below include only the selected upstream schema packages;
protobufs from AndroidX, Tink, Google Play services, and other APK dependencies
are excluded. The final column validates the ground-truth corpus inputs, not
ProtoLoom's recovered-output compile rate; targets with no matched recovery have
no recovery compile denominator.

| App and selected truth | Field recall | Precision | Wire accuracy | Type fidelity | Names | Structure | Enums | Truth source compile |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bitwarden Authenticator, `google_authenticator.proto` | 100% (12/12) | 100% (12/12) | 100% (12/12) | 75% (9/12) | 100% (12/12) | 0% (0/1) | 0% (0/5) | 100% (1/1) |
| Meshtastic, three selected files | 0% (0/483) | n/a (0 recovered) | n/a | n/a | n/a | 0% (0/132) | 0% (0/449) | 100% (3/3) |
| Flipper, three selected schema groups | 0% (0/36) | n/a (0 recovered) | n/a | n/a | n/a | 0% (0/2) | 0% (0/20) | 100% (3/3) |
| Gadgetbridge, three selected files | 0% (0/115) | n/a (0 matched) | n/a | n/a | n/a | 0% (0/16) | 0% (0/27) | 100% (3/3) |
| Smartspacer, `smartspace.proto` | 100% (37/37) | 100% (37/37) | 100% (37/37) | 48.65% (18/37) | 97.30% (36/37) | 0% (0/7) | 0% (0/27) | 100% (1/1) |

Bitwarden's first manifest entry mistakenly paired the Authenticator schema
with the Password Manager APK. The manifest now pins the matching Authenticator
release; this is why its current strict run has 104 output files rather than the
unrelated Password Manager run's 37.

Meshtastic's pinned Android dependency identifies its schema library as
Wire-generated Kotlin multiplatform models. Those selected messages therefore
do not expose the Java-lite `newMessageInfo` path that ProtoLoom v0.1 targets;
the 52 recovered files belong to unrelated lite dependencies. Flipper likewise
contains none of the selected messages as recoverable lite metadata or embedded
descriptors, making its zero a measured unsupported-runtime result rather than
a missing comparison.

Gadgetbridge's 20 outputs include generated empty Garmin messages, but none of
the 115 selected truth fields matched; its field-bearing calls are among the 671
strict bail-outs. Smartspacer and Bitwarden are valid positive diffs. Both show
perfect wire accuracy on matched fields while preserving the already-reported
enum and nesting gaps. These extraction rows retain a zero round-trip
denominator. Bitwarden is measured separately against a real payload in Tier C.

As a separate container-layer oracle, all 35,762 strings in Mullvad's primary
DEX matched androguard 4.x in order and value. Reproduce that check with
`uv run --with androguard python scripts/check_dex_oracle.py <apk>`.

Mullvad's pinned `management_interface.proto` provides matching ground truth.
The comparison covers 112 truth messages and 297 truth fields. Rerun on
2026-08-27 after two fixes: wiring DEX `EnclosingClass` annotations into the
recovered message tree, then recovering proto3's synthetic single-field
oneofs from the hasbit signal. Before either fix, structural fidelity was
16.36% (9/55) and field recall/precision were 90.91% (270/297); after the
first fix alone, structural fidelity was 52.73% (29/55):

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 100.00% | 297 / 297 |
| Field precision | 100.00% | 297 / 297 |
| Wire-type accuracy | 100.00% | 297 / 297 |
| Type fidelity | 85.19% | 253 / 297 |
| Name recovery | 87.21% | 259 / 297 |
| Label accuracy | 100.00% | 297 / 297 |
| Structural fidelity | 96.36% | 53 / 55 |
| Enum recovery | 100.00% | 106 / 106 |
| Compile rate | 100.00% | 1 / 1 target |
| Round-trip rate | not measured | 0 payloads |
| Type-fidelity ceiling | 99.66% | 296 / 297 fields |

Reproduce the comparison with `scripts/diagnose_real_app.py` after compiling
the pinned source proto to a descriptor set. Enum recovery uses retained getter
return types to identify generated enum classes, then requires constructor and
matching static-field-store evidence from each enum initializer. Structural
recovery reads each class's `dalvik.annotation.EnclosingClass` system
annotation and rebuilds the real parent/child message tree instead of
flattening every class to a top-level message; the earlier field
recall/precision loss turned out to be downstream of that same flattening
(mismatched nested-message names broke field-to-message matching), which is
why those two metrics also moved to 100%. The remaining oneof-shaped share of
the gap came from proto3 `optional` fields: they compile to a synthetic
one-member oneof for presence tracking, and protobuf-lite's info string marks
that with a hasbit rather than a real `oneof_index`, so the field-level hasbit
signal is now read back into a synthetic oneof when the schema is proto3 and
the field carries no real oneof index. The 2 remaining structural misses are a
same-simple-name collision (two distinct DEX classes named `Relay` in
different packages get merged by name during reconciliation) — a known,
narrower gap, not yet fixed.

The published ceiling is intentionally unflattering. Only one of Mullvad's 297
fields is capped by the roadmap's scalar-varint ambiguity model, so the
14.47-point gap between measured type fidelity and the 99.66% ceiling is mostly
recoverable implementation loss, not an information-theoretic excuse. Name and
structural ceilings are not assigned a made-up corpus-wide number: both depend
on whether each target's optimizer retains field strings, class references,
oneof metadata, and enclosing-class identity. Their measured scores remain the
honest numbers until the evidence model records those per-field observability
facts.

## Tier C captured payload

Tier C now contains one real application payload. Google Authenticator exported
one intentionally disposable TOTP account through its account-transfer QR flow
on an Android emulator. The QR was decoded locally into a 63-byte
`MigrationPayload`; the screenshot and URI are not retained. The fixture pins
the payload, Bitwarden's upstream `google_authenticator.proto` descriptor, the
descriptor emitted from ProtoLoom's Bitwarden recovery, and their provenance.

| Schema used for validation | Decode | Semantic round trip | Byte-identical round trip |
|---|---:|---:|---:|
| Pinned upstream Bitwarden schema | 100% (1/1) | 100% (1/1) | 0% (0/1) |
| ProtoLoom-recovered Bitwarden schema | 100% (1/1) | 100% (1/1) | 0% (0/1) |

Both serializers produce 61 bytes from the 63-byte capture. The byte-level
change is canonicalization: Google Authenticator explicitly encoded the proto3
default `batch_index = 0`, while deterministic serialization omits that field.
This is semantic success and byte-identity failure, not an inflated strict
score. One payload exercises only one message path; it does not prove the
entire recovered schema correct.

The pinned Mullvad and Signal source trees still contain no qualifying captured
payload. Tier A's synthetic byte-identical round trip remains separate from this
real Tier C result.

## pbtk differential

pbtk 1.1.2 was run against the same pinned Mullvad APK using its standalone
`pbtk-jar-extract` command. It recovered and emitted 25 proto files in 8.7
seconds. Its three Mullvad application schemas compiled with protoc 29.3. On
the matching `management_interface.proto` ground truth, pbtk recovered all 112
messages, 297 fields, 55 structural relationships, and 106 enum values exactly:

| Metric | ProtoLoom | pbtk 1.1.2 |
|---|---:|---:|
| Field recall | 100.00% | 100.00% |
| Field precision | 100.00% | 100.00% |
| Wire-type accuracy | 100.00% | 100.00% |
| Type fidelity | 85.19% | 100.00% |
| Name recovery | 87.21% | 100.00% |
| Label accuracy | 100.00% | 100.00% |
| Structural fidelity | 96.36% | 100.00% |
| Enum recovery | 100.00% | 100.00% |
| Compile rate | 100.00% | 100.00% |

This is the less flattering result and the important one: ProtoLoom is already
useful as a small direct parser with explicit uncertainty, but it does not yet
match pbtk's schema fidelity on this unobfuscated real app. The pinned adapter
is `scripts/pbtk_1_1_2_adapter.sh`; `scripts/compare_pbtk.sh` records isolated
tool logs and statuses for a directory of artifacts.
