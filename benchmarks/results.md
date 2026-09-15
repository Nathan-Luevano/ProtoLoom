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
| Structural fidelity | n/a | n/a | n/a |
| Enum recovery | n/a | n/a | n/a |
| Compile rate | 100.00% | 100.00% | 100.00% |
| Round-trip rate | 0.00% | 0.00% | 0.00% |

Corpus: `tier-a-small` local calibration fixture. Targets: 1. Reproduce it with
`protoloom bench --corpus tier-a-small --per-target`. The manifest hash-pins
both inputs.

The calibration schema has no nesting, oneofs, or enums, so those denominators
are zero and are reported as unmeasured. The harness now excludes every
zero-denominator metric from macro and micro aggregation, rather than treating
the empty ratio as a vacuous 100%; per-target output uses `n/a` consistently.
The same rule applies to the type-fidelity ceiling: for example, Tier A's
enum-only `googleapis-month` target has no field ceiling to measure, while the
aggregate remains 100% across the twelve targets that do have fields.

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
| Generated C++ object recovery | 100.00% | 3 / 3 objects |
| Generated C++ descriptor byte identity | 0.00% | 0 / 3 objects |
| Round trip | not measured | 0 payloads |

The perfect root scores are Path-1 results, not a claim that descriptor-free
generated code is solved. A fresh protoc 29.3 rerun corrected the compiled C++
classification: conformance, Google Date, and gRPC Health objects all expose a
standalone serialized descriptor accepted by the scanner. Their generated-code
descriptors omit redundant `json_name` fields added by
`--descriptor_set_out`, so raw byte identity is 0/3 even though root recovery
is 3/3. `measurements.json` now records recovery and identity separately. No
payloads belong to this corpus, so the benchmark renderer's vacuous 100% for a
zero round-trip denominator is not used as a result.

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

## Descriptor-free Path-2 evidence

`scripts/run_path2_go.sh` builds a Go 1.24 binary with `-trimpath` and
`-ldflags='-s -w'` from a runtime-compatible legacy tagged message. The scanner
first proves that the binary contains zero serialized descriptors. Go's linked
runtime type table still associates `Record` with each field's exact Go type,
source field name, offset, and protobuf struct tag. ProtoLoom follows those
links rather than scanning loose strings and recovers all three fields exactly:

| Target | Embedded descriptors | Field recall | Type fidelity | Labels | Compile |
|---|---:|---:|---:|---:|---:|
| Go 1.24 tagged ELF | 0 | 100% (3/3) | 100% (3/3) | 100% (3/3) | 100% (1/1) |

The reader is intentionally gated to little-endian 64-bit Go 1.24 metadata.
Unsupported versions and malformed type-link tables bail out; ambiguous tagged
fields prevent that message from being emitted. The fixture reports one honest
bail-out for the legacy runtime's unrelated `MessageSet` type while recovering
the selected message. Modern `protoc-gen-go` 1.36.6 binaries still retain a
gzip descriptor and therefore remain Path-1.

`scripts/run_path2_cpp.sh` establishes the C++ lite boundary with two optimized,
LTO-linked, section-GC'd, fully stripped binaries. Both have zero descriptors.
Their schemas differ in two scalar source-field names, but after removing build
IDs and normalizing generated source paths the binaries are byte-identical.
Only the string field's UTF-8 diagnostic name survives. Exact recovery of the
two differing scalar names is therefore a confirmed information limit, and no
C++ lite schema is emitted rather than inventing names. Ordinary full-runtime
protoc 29.3 C++ objects are the 3/3 Path-1 cases above, not Path-2 inputs.

## Pinned javalite matrix

`scripts/run_lite_matrix.sh` compiles the hostile local schema with protoc and
protobuf-javalite 3.21.12, 4.29.3, and 4.35.1, packages each runtime with D8
8.3.37, and also runs default and aggressive R8 legs for 4.35.1. Every archive
is SHA-256 pinned. The same generated payload exercises scalars, packed values,
nested messages, repeated messages, a map, an enum, a oneof, and proto3
optional presence.

All five legs share the metrics in the first table. Optimizer-sensitive name,
type, and enum fidelity are shown per leg in the second table. Schema counts
differ because the D8 archives include the complete javalite runtime. Every
unresolved or opt-in heuristic call is counted as a bail-out or emitted schema
instead of disappearing behind a medium-confidence finding:

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 100.00% | 13 / 13 |
| Field precision | 100.00% | 13 / 13 |
| Wire-type accuracy | 100.00% | 13 / 13 |
| Label accuracy | 100.00% | 13 / 13 |
| Structural fidelity | 100.00% | 2 / 2 |
| Compile rate | 100.00% | 1 / 1 target |
| Round-trip rate | 100.00% | 1 / 1 payload |
| Type-fidelity ceiling | 92.31% | 12 / 13 fields |
| Counted uncertainty | see below | every unresolved or heuristic call |

| Leg | Recovered schemas | Name recovery | Type fidelity | Enum recovery |
|---|---:|---:|---:|---:|
| javalite 3.21.12 + D8 | 28 | 100% (13 / 13) | 100% (13 / 13) | 100% (2 / 2) |
| javalite 4.29.3 + D8 | 62 | 100% (13 / 13) | 100% (13 / 13) | 100% (2 / 2) |
| javalite 4.35.1 + D8 | 64 | 100% (13 / 13) | 100% (13 / 13) | 100% (2 / 2) |
| javalite 4.35.1 + default R8 | 2 | 100% (13 / 13) | 100% (13 / 13) | 100% (2 / 2) |
| javalite 4.35.1 + aggressive R8 | 2 | 84.62% (11 / 13) | 69.23% (9 / 13) | 0% (0 / 2) |

Heuristic lite recovery is disabled by default. The matrix opts in with
`--allow-heuristic-lite` to measure the recoverable floor, and the report still
counts each emitted heuristic call. Without the flag those calls bail out and
emit no guessed schema.

These numbers score the emitted descriptor set, like the real-app rows, rather
than the intermediate recovery JSON. The JSON evaluator now recurses into
nested messages, but it does not represent the emitter's final qualified type
resolution. The round-trip checker isolates the selected matrix package, so
unrelated runtime descriptors cannot prevent validation of the payload schema.

The JSON evaluator's nested-message qualified names were also wrong until
directly caught: `_recovered_message` named every message `package.BareName`
regardless of nesting depth, instead of building on its parent's name the
way `_truth_messages` and the `.desc` path already do. Two same-named
messages nested under different parents collided, and a truth message
qualified by its real ancestry (e.g. `pkg.Outer.Inner`) never matched a
same-named recovered message even when the content was fully correct — this
depressed field recall and, more visibly, structural fidelity on schemas
with deep or broad nesting (Signal's real numbers looked as low as 73%/33%
through this path before the fix, against the true, `.desc`-verified
100%/100%). The `.desc` path was never affected; this only ever hit the
JSON fallback, which is why every published real-app row in this document
was scored `.desc`-to-`.desc` rather than JSON. Fixed to build the same
parent-qualified name, with a regression test covering the exact collision
case.

The aggressive R8 leg inlines and renames `newMessageInfo`; recovery succeeds
by recognizing the validated `RawMessageInfo` constructor shape. Message class
names are obfuscated, while this configuration leaves the field-name strings
intact. Default R8 recovers 13/13 names; aggressive R8 recovers 11/13 because
two names depend on class identity that its configuration removes. The result
measures call-shape and class-name damage, not a general loss of metadata
field-name strings.
Unobfuscated getter return types now identify the enum class, and its static
initializer proves both value names and numbers through constructor arguments
and matching static-field stores. That moves enum recovery from 0/2 to 2/2 on
all D8 legs and default R8. Aggressive R8 renames the getter and removes that
field-to-enum association, so it honestly remains 0/2. D8 also retains the
`MapEntryLite.newDefaultInstance` call and exact `WireFormat.FieldType` static
fields, recovering `map<string, int32>`. Final descriptor emission resolves all
qualified message and enum references. R8 inlines the map factory into a
two-argument constructor, but each argument is a static field whose initializer
still proves the canonical `STRING` and `INT32` names. Following that exact
constructor/store chain brings default R8 to 13/13 and aggressive R8 to 9/13.
Aggressive R8 still loses nested-class and enum associations. Both original
nesting relationships are recovered, for 2/2 structure.
The required Mullvad, Bitwarden, and Smartspacer reruns stayed byte-identical
at 129/0, 104/0, and 70/0 respectively after this map change.

A further aggressive-R8 inspection found an exact accessor chain from the
message's integer storage field through the enum's `forNumber` equivalent.
The enum initializer also retains constructor literals and numbers even though
R8 renames its static fields. ProtoLoom now emits `MODE_UNSPECIFIED = 0` and
`MODE_ACTIVE = 1` under the surviving obfuscated type name `z0`. The source
type identity `Mode` does not survive, so type fidelity remains 9/13 and enum
recovery remains 0/2; deriving `Mode` from the `MODE_` value prefix would be a
guess. The other accessor candidate is a oneof-case enum and is rejected by
its retained `_NOT_SET` value. A fresh five-leg run reproduced every table
value above. Mullvad and Smartspacer remained descriptor-identical at 129/0
and 70/0. Bitwarden remained 104/0; its combined dependency descriptor gained
obfuscated enum values, while its selected normalized schema stayed at 100%
on all nine extraction metrics.
The ceiling applies the roadmap's declared ambiguity between the
`int32`/`sint32`/`uint32` and `int64`/`sint64`/`uint64` families. The remaining
aggressive R8 gap combines optimizer-sensitive recovery loss with a confirmed
identity limit: the enum's values and field association survive, but its source
type name does not. D8 and default R8 exceed the wire-only ceiling using
retained declared-type evidence.

## Tier B real-app runs

The eight hash-pinned APKs in `benchmarks/corpus/tier-b-real-apps.json` were
downloaded, verified, and rerun directly without jadx under the current strict
default. Signal and Molly remain a deliberate fork pair and count as one schema
family, giving seven independent families across eight shipping artifacts.
Bitwarden and Mullvad were rerun on 2026-08-17 after the nested-enum recovery
change; their rows below contain those newer results.

| App | Output schemas | Bail-outs | Strict-default result |
|---|---:|---:|---|
| Signal 8.22.2 | 789 | 0 | selected Wire schemas recovered; 2 gRPC services |
| Molly 8.19.2-4 | 589 | 0 | selected Wire schemas recovered |
| Mullvad 2026.8 | 130 | 0 | recovered; 1 gRPC service (94 RPCs) |
| Bitwarden Authenticator 2026.7.1 | 104 | 0 | recovered |
| Meshtastic 2.8.1 | 289 | 0 | selected Wire schemas partially recovered |
| Flipper 1.8.1.1890 | 61 | 0 | selected Wire `Settings` recovered |
| Gadgetbridge 0.93.0 | 646 | 0 | recovered |
| Smartspacer 1.11.2 | 70 | 0 | recovered with explicit enum uncertainty |

Square Wire recovery is now a separate extraction path. Signal and Molly retain
exact `WireField` annotations; Meshtastic's generated adapters retain field
loads, adapter identities, literal tags, and `encodeWithTag` calls after its
annotations are stripped. Flipper's R8 output retains the same adapter-write
shape and exact source field spellings in generated `toString` strings. Wire
enum initializer stores, syntax constants, boxed proto3 presence, and the exact
constructor diagnostic for oneof membership are consumed only when their
compiled evidence survives.

The path does not use protobuf-lite `newMessageInfo`, and it does not infer
absent source identities. Every fresh run above has zero bail-outs. The prior
lite-only counts remain useful historical coverage results, but no longer
describe the selected Wire schemas.

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
| Signal, two `protowire` files | 100% (551/551) | 100% (551/551) | 100% (551/551) | 100% (551/551) | 98.91% (545/551) | 100% (110/110) | 100% (160/160) | 100% (2/2) |
| Molly, two `protowire` files | 100% (546/546) | 100% (546/546) | 100% (546/546) | 100% (546/546) | 98.90% (540/546) | 100% (110/110) | 100% (159/159) | 100% (2/2) |
| Bitwarden Authenticator, `google_authenticator.proto` | 100% (12/12) | 100% (12/12) | 100% (12/12) | 75% (9/12) | 100% (12/12) | 0% (0/1) | 100% (5/5) | 100% (1/1) |
| Bitwarden, package-normalized | 100% (12/12) | 100% (12/12) | 100% (12/12) | 100% (12/12) | 100% (12/12) | 100% (1/1) | 100% (5/5) | 100% (1/1) |
| Meshtastic, three selected files | 91.77% (446/486) | 100% (446/446) | 100% (446/446) | 100% (446/446) | 99.78% (445/446) | 93.28% (125/134) | 77.92% (353/453) | 100% (3/3) |
| Flipper, three selected schema groups | 66.67% (24/36) | 100% (24/24) | 75% (18/24) | 75% (18/24) | 100% (24/24) | 0% (0/2) | 0% (0/20) | 100% (3/3) |
| Gadgetbridge, three selected files, package-normalized | 100% (115/115) | 100% (115/115) | 100% (115/115) | 100% (115/115) | 63.48% (73/115) | 100% (16/16) | 100% (27/27) | 100% (3/3) |
| Smartspacer, `smartspace.proto` | 100% (37/37) | 100% (37/37) | 100% (37/37) | 86.49% (32/37) | 100% (37/37) | 100% (7/7) | 7.41% (2/27) | 100% (1/1) |

Bitwarden's first manifest entry mistakenly paired the Authenticator schema
with the Password Manager APK. The manifest now pins the matching Authenticator
release; this is why its current strict run has 104 output files rather than the
unrelated Password Manager run's 37.

The two Bitwarden rows separate exact source-package identity from schema
recovery under the compiled Java namespace. The first retains the source's
absent proto package and therefore includes the confirmed package-information
limit described below. The second explicitly normalizes that package and
scopes recovery to `MigrationPayload`. It moved from 91.67% (11/12) to 100%
type fidelity when repeated-message resolution stopped trusting an
R8-obfuscated Java list-wrapper type (`Lxy2;`) over the exact
`OtpParameters.class` literal retained in the objects array. Mullvad's
descriptor remained byte-identical, and Smartspacer's selected metrics did not
change.

Signal and Molly were scored separately against the exact schema trees pinned
for their releases. Signal recovers 551/551 selected fields and Molly 546/546,
both with 100% precision, wire accuracy, exact types, labels, and enum values.
Six source names in each APK retain only their compiled spelling. Each fork
scores 110/110 structural relationships. Both emitted selected descriptor
groups compile 2/2. Molly's
`SignalService.proto` is byte-identical to Signal's pinned source, while its
different `StorageService.proto` was fetched and compiled from Molly commit
`0e202dda90785771ff8c87526eaaf30c655f54f3` rather than reused from Signal.

The two recovered relationships are empty nested messages referenced by exact
field adapters. Both APKs retain the child classes as direct Wire `Message`
subclasses and retain their exact DEX `EnclosingClass` parents. This closes the
two prior type misses without inferring a container from source truth.

The four remaining relationships were proto3 optional presence. Their retained
`schemaIndex` values identify exact constructor parameters, and Kotlin's
synthetic default constructors set those parameters to literal null under the
corresponding mask bits. ProtoLoom evaluates that compiled default path and
accepts only scalar or enum fields; nullable message fields are not relabeled.

Meshtastic's retired `v2.8.1-internal.3` asset, release API entry, and Git tag
all return 404 upstream, so the corpus now pins the available stable `v2.8.1`
universal APK instead. The downloaded 20,794,130-byte artifact independently
hashes to `e7ff1c1f9bd59142efd37a47ddb3ac9b05b0e65788f31478a5823b658d66fa0f`.
Its release commit pins `org.meshtastic:protobufs` version
`2.7.26.151-gef0ae57-SNAPSHOT`, resolved to full schema commit
`ef0ae579e9de937f7fae25f9fdc5dbfe24a2fe2b`; the refreshed truth denominators
come from that exact tree.

Meshtastic strips `WireField` annotations, so recovery follows its generated
adapter bytecode. The selected files recover 446/486 fields with no extras.
Six external enum references now retain their exact nested owner path. DEX
lineage identifies both the enum owner and each recovered enclosing message;
ProtoLoom qualifies a reference only through that complete declared chain.
This moves wire accuracy from 439/446 to 445/446 and exact types from 428/446
to 434/446 without inventing a scope for an absent owner. The final wire miss
was a one-value enum whose initializer uses Java's standard `(String, int)`
enum constructor. Accepting its exact three-register invocation recovers the
retained `UNUSED = 0`, closing wire accuracy at 446/446 and moving exact types
to 435/446 and enum values to 353/453.
The structural denominator contains 124 proto3 oneof groups, 116 of which are
synthetic single-field groups for optional presence. Meshtastic's generated
models retain boxed JVM types for 107 of the previously missed scalar fields.
Applying that compiled presence proof to the adapter path moves structural
fidelity from 18/134 to 125/134; unboxed fields and absent messages remain
unclassified.
Nine message fields retained flattened DEX names even though their child
declarations and complete enclosing chains were recovered. Resolving those
references only after every attached message has its final name moves exact
types from 435/446 to 444/446. Mullvad remains perfect on all nine metrics;
the same declaration-aware pass removes 19 duplicate placeholder messages.
The evaluator previously let absent truth message `Compressed` greedily claim
the recovered `NodeRemoteHardwarePin` because both share the same two-field
wire signature. Reserving exact-name matches before structural fallbacks stops
that false attribution and reports the recovered fields honestly: 446/446
exact types and 445/446 exact names.
The 40 missing fields belong to ten source messages with no corresponding
generated class in the pinned APK; similarly named generated classes are not
renamed to them. All 446 recovered fields now have exact type identity. The
remaining name miss retains only `data_` in both the field and `toString`
diagnostic. Seven structural misses belong to absent messages; the other two
are an ordinary nullable message and a string with no retained default path,
so neither proves proto3 optional presence. The missing `CriticalErrorCode`,
`ExcludedModules`, and `TelemetrySensorType` classes account for 88 absent
enum values, and `RemoteShell`'s absent class accounts for the final 12. Those
gaps stay explicit rather than being filled from source truth.

Flipper's obfuscated `Settings` model retains all 24 source field strings and
tagged writes. Six enum-typed fields recover only their surviving obfuscated
types, because the source enum type names and value identities are absent;
this yields 18/24 exact types and 0/20 enum values. The selected wearable
`MainRequest`, `MainResponse`, and `WearableSyncItemData` classes and source
names are absent from the release DEX, so their 12 fields remain unrecovered.
The combined 24/36 result does not infer them from related RPC classes.

Gadgetbridge's field-bearing calls used explicit array sizes and indexes, but
the indexes `0` and `1` were constants established before `dynamicMethod`'s
packed switch. The linear instruction scan then walked unrelated switch arms
and replaced those registers before reaching the `newMessageInfo` arm, falsely
classifying every affected objects array as unresolved-order. Restoring the
pre-switch register state at each encoded packed-switch target moved a fresh
strict run from 20 files and 626 bail-outs to 646 files and zero bail-outs.
Smartspacer's two remaining bail-outs had the same shape and also moved to zero;
Mullvad and Bitwarden stayed at 129/0 and 104/0 respectively.

The selected Gadgetbridge messages now have perfect field, wire, type, label,
structure, and enum scores. Name recovery remains 73/115 because Huami's Java
generator camel-cases source fields such as `startTimestamp`, while the
compiled field strings retain only `startTimestamp_`; reversing that spelling
to the source's exact underscore/case form is not generally lossless. Scoring
normalizes each truth file's proto package to its compiled `java_package` and
filters the recovered descriptor to the selected top-level messages. This is
necessary because `gdi_core.proto` uses `garmin_vivomovehr` while its Java
classes use `nodomain.freeyourgadget.gadgetbridge.proto.garmin`, and
`huami.proto` declares no proto package at all. The selected messages and all
of their fields are matched by compiled class identity; the original proto
package remains the same lite-runtime information limit described for
Bitwarden below.

Both normalized views are directly reproducible with
`scripts/diagnose_real_app.py --normalize-truth-package
--scope-to-truth-roots`; they no longer require temporary descriptor rewriting
or manual recovered-message filtering.

Smartspacer and Bitwarden show perfect wire accuracy on matched fields.
Smartspacer's nested-message
structure recovers cleanly now that enclosing-class recovery falls back to
the DEX name shape when `EnclosingClass` annotations are stripped; Bitwarden's
one message-nesting relationship (`OtpParameters` inside `MigrationPayload`)
also recovers correctly through the same path — its earlier 0/1 was
misdiagnosed as an obfuscation-driven annotation/name-shape loss. Directly
re-investigated, the real cause is a package-qualification mismatch: this
proto has no `package` statement, but the Java classes still live under
`option java_package`'s namespace, so ProtoLoom's recovered schema carries
that namespace as its package while truth's structural comparison expects
the bare (unqualified) parent name. This is a genuine, narrow ambiguity —
nothing in the compiled artifact says whether an unqualified proto and a
`java_package`-only build are the same thing or a real divergent
`java_package` override.

Whether any other DEX-level signal could resolve it was investigated
directly rather than left open by assumption: `MigrationPayload`'s compiled
class carries `dynamicMethod`, no `getDescriptor()`/`DESCRIPTOR` accessor,
and no embedded byte array of a serialized `FileDescriptorProto` — the
hallmarks of `GeneratedMessageLite`, which deliberately carries no runtime
descriptor at all (that's the whole efficiency point of the lite runtime).
The original `.proto`'s `package` statement, distinct from the Java
namespace `java_package` resolves to, simply isn't represented anywhere in
a lite build's compiled output. For lite-recovered schemas this is a
confirmed information limit, not an unexplored one; a full (non-lite)
runtime target embedding real descriptor bytes could resolve it, but that's
a different code path than anything covered here. These extraction rows
retain a zero round-trip denominator.
Bitwarden is measured separately against a real payload in Tier C.

Bitwarden's enum recovery moved from 0/5 to **100.00% (5/5)** the same
session as a byproduct of nesting file-scope enums correctly: `Algorithm` and
`OtpType` are declared directly inside `MigrationPayload`, as siblings of
`OtpParameters` rather than nested inside it, so the field that uses them
(`OtpParameters.type`) doesn't own them structurally. The enum-recovery
mechanism itself already worked and had for some time — the values were
right — but the recovered enum was attached at file scope instead of inside
`MigrationPayload`, which is exactly the kind of name/parent mismatch the
`enum_recovery` metric's value-set matching requires the right owner for.
The same `EnclosingClass`-or-name-shape technique built for message nesting
now applies to a non-message-local enum's own enclosing class, attaching it
to whichever recovered message the DEX identifies as its real parent, with
the same redundant-prefix rename applied (`MigrationPayload_OtpType` ->
`OtpType`) once that parent is known.

Smartspacer's enum recovery (0/27) was investigated directly rather than
assumed: `SmartspaceCard`'s own class has *no getter method at all* for
`card_type` (nor does its `Builder` retain a setter with the enum parameter
type), so the getter-return-type technique that proves Mullvad's and
Bitwarden's enums has no accessor to read in the first place — the same
failure mode the javalite matrix's aggressive-R8 leg already documents.

An accessor-independent source does exist, though: `newMessageInfo`'s
objects array carries a reference to the field's generated
`Internal.EnumVerifier` singleton (`CardTypeVerifier.INSTANCE`) even for
enum fields with no getter, and that Verifier class is always compiled as a
nested class of the real enum (`CardType$CardTypeVerifier`) — its own
enclosing class *is* the enum, independent of any accessor. Reading that
reference now recovers `card_type` (moving enum recovery to **7.41%,
2/27**) with the same constructor/static-field-store proof already used for
getter-based recovery, just handed a different starting descriptor.

It stops at 2/27, not more, because R8 merges several distinct `Verifier`
classes into one physical DEX class in this build, keeping their singletons
apart only by field name (`INSTANCE`, `INSTANCE$1`, `INSTANCE$2`, ...) —
`card_priority`'s own `CardPriorityVerifier` was absorbed into the same
class that still carries `CardTypeVerifier`'s name, so its `enclosing class
== enum` equivalence no longer holds once merged. Only the unqualified
`INSTANCE` field is trusted as evidence for exactly this reason: it is the
one singleton that still names the class it actually lives in; the numbered
siblings belong to an absorbed verifier and would misattribute the enum if
trusted, so those fields are left unresolved rather than guessed. The
remaining 25/27 need a way to disambiguate a merged Verifier class's
absorbed singletons.

That disambiguation was investigated directly, not left as a guess: reading
the merged class's own `<clinit>` shows all five singletons (`INSTANCE`
through `INSTANCE$4`) built the identical way — `new CardTypeVerifier()`
with a no-argument constructor, then an `sput-object` into each field, no
discriminator value passed or stored anywhere. The class also declares no
`isInRange`-style method at all beyond `<clinit>`; R8 evidently proved the
verifier's actual check was dead code (inlined and eliminated at every call
site) and stripped the method bodies entirely, leaving five behaviorally
identical, structurally indistinguishable marker objects. There is no
surviving signal in the compiled artifact — not a constructor argument, not
a per-instance method, not a discriminator field — that ties a numbered
singleton back to its original enum. This is now a confirmed information
limit for merged-Verifier singletons, not an unexplored one.

As a separate container-layer oracle, all 35,762 strings in Mullvad's primary
DEX matched androguard 4.x in order and value. Reproduce that check with
`uv run --with androguard python scripts/check_dex_oracle.py <apk>`.

Mullvad's pinned `management_interface.proto` provides matching ground truth.
The comparison covers 112 truth messages and 297 truth fields. Rerun on
2026-08-27 after three fixes: wiring DEX `EnclosingClass` annotations into the
recovered message tree, recovering proto3's synthetic single-field oneofs
from the hasbit signal, and reading message-typed fields' real type from the
owning class's own declared field type instead of the `newMessageInfo`
objects array. Before any of it, structural fidelity was 16.36% (9/55), field
recall/precision were 90.91% (270/297), and type fidelity was 54.07%
(146/270); after the first two fixes, structural fidelity was 96.36% (53/55)
and type fidelity was 85.19% (253/297):

| Metric | Result | Count |
|---|---:|---:|
| Field recall | 100.00% | 297 / 297 |
| Field precision | 100.00% | 297 / 297 |
| Wire-type accuracy | 100.00% | 297 / 297 |
| Type fidelity | 100.00% | 297 / 297 |
| Name recovery | 100.00% | 297 / 297 |
| Label accuracy | 100.00% | 297 / 297 |
| Structural fidelity | 100.00% | 55 / 55 |
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
why those two metrics also moved to 100%. Another share of the earlier gap
came from proto3 `optional` fields: they compile to a synthetic one-member
oneof for presence tracking, and protobuf-lite's info string marks that with a
hasbit rather than a real `oneof_index`, so the field-level hasbit signal is
now read back into a synthetic oneof when the schema is proto3 and the field
carries no real oneof index. Both structural fidelity and type fidelity had 2
remaining misses each, and both sets came from the same cause: two distinct
DEX classes named `Relay` in different packages
(`mullvad_daemon.management_interface` and `mullvad_daemon.relay_selector`)
were merged into one schema by `reconcile.py`'s name-only merge key, so the
real `Relay`'s enclosing-class lookup for its `WireguardEndpoint` field
resolved against the wrong class's identity. The merge key is now
`(package, name)` instead of bare `name`, which still merges the same class
recovered from multiple DEX files (same package, same name) while no longer
conflating unrelated classes that happen to share a bare file name. Both
metrics moved to 100.00% (55/55 and 297/297) with this one fix, and nothing
else changed — Smartspacer's numbers are identical before and after.

Type fidelity closed its well-known-type gap in the same session: 9 fields
typed `google.protobuf.Timestamp`, `Duration`, or `StringValue` were guessed
from the field's own name because that resolution deliberately excluded
`Lcom/google/protobuf/...;` descriptors — correctly avoiding the alternative
failure mode where the field's declared type is a `repeated` field's list
wrapper (e.g. `ProtobufArrayList`), not its element type, but throwing out
real well-known types along with it. A fixed lookup table now recognizes the
well-known types protobuf itself ships (`Any`, `Empty`, `Duration`,
`Timestamp`, `FieldMask`, `Struct`/`Value`/`ListValue`, and the wrapper
types), resolves them to their fully-qualified `.google.protobuf.X` name, and
records the matching `import "google/protobuf/....proto";` line via
`RecoveredSchema.dependencies` — while every other `com.google.protobuf.*`
declared type (list wrappers included) still falls through to the
objects-array class literal or an honest guess, exactly as before.

Name recovery went through three fixes in the same session and finished at
100.00% (297/297), up from 85.93% (232/270) at the start of this document's
history. First, from 87.21% to 89.90% (259/297 -> 267/297): a real `oneof`
member shares one storage field with its siblings and so never gets its own
name string in the `newMessageInfo` objects array, only a class literal for
its type; when that class descriptor carries at least two DEX `$` levels —
genuine nesting, e.g. `TunnelState$Connected` — the field name is now
derived from the type's bare local name (`connected`). Second, from 89.90%
to 90.24% (267/297 -> 268/297): `java_to_proto_name` was escaping every
protobuf directive keyword (`message`, `class`, ...) as if field identifiers
couldn't be named that, appending a trailing `_` — but real protoc (verified
against the pinned 29.3 binary) accepts `string message = 1;`,
`message message {}`, `enum enum {}`, and `oneof oneof {}` without complaint;
`emit/proto.py` already escapes independently at render time, so the
decode-level escaping was both redundant and wrong, corrupting the recovered
model's own field name (`message_` instead of `message` on `LogMessage`).
Both escaping mechanisms are now gone.

The third fix closed the rest of the gap in one step, from 90.24% to
**100.00%** (268/297 -> 297/297): protobuf-lite emits a
`NAME_FIELD_NUMBER` static `int` constant per field — including oneof
members, which is exactly the case the first fix could only partially cover.
Its own name is generated directly from the original proto field name
uppercased, so lowercasing it recovers the true name losslessly, unlike
reversing a getter's camelCase, which can't tell whether a digit-letter
transition in the original name (`Udp2Tcp`) had an underscore (`udp2_tcp`)
or not (`udp2tcp`) — this is what closed that specific case, plus the
remaining single-`$`-level flat-oneof names (`RelaySettings.custom`,
`AccessMethod.custom`, `DaemonEvent`'s 8 members, and more) that the local
name-derivation heuristic in the first fix couldn't reach at all. Reading it
needed two new `DexFile` primitives — `class_static_fields` and
`static_field_values`, parsing the DEX `class_data_item`'s static-field list
and its `static_values_off` encoded array — verified with a hand-built DEX
fixture before use on real APKs. The constant is also immune to the
short-name false positive in the obfuscation heuristic (a lone two-letter
field like `id_` would otherwise get treated as obfuscated); an
authoritative `*_FIELD_NUMBER` name now bypasses that check. Confirmed on
Smartspacer (name recovery 97.30% -> 100.00%) and on Bitwarden
(100.00%, 12/12).

The published ceiling is intentionally unflattering. Only one of Mullvad's 297
fields is capped by the roadmap's scalar-varint ambiguity model, so the
0.34-point gap between measured type fidelity and the 99.66% ceiling is not
an information-theoretic shortfall — it's the model correctly resolving a
field the ceiling's wire-only reasoning treats as ambiguous, using signals
(the field's own declared type) beyond raw wire behavior. Name and structural
ceilings are not assigned a made-up corpus-wide number: both depend on
whether each target's optimizer retains field strings, class references,
oneof metadata, and enclosing-class identity. Their measured scores remain
the honest numbers until the evidence model records those per-field
observability facts.

## gRPC service recovery

ProtoLoom recovers `service`/`rpc` definitions from compiled `protoc-gen-
grpc-java` stubs, not just message and enum shapes. The generated `<Service>
Grpc` holder class gives each RPC a static `get<Name>Method()` building and
caching a `MethodDescriptor`; that method's bytecode carries the RPC name
(a `const-string`, cross-validated against the one shared `SERVICE_NAME`
string repeated across every method in the class), the request and response
types (the first two `getDefaultInstance()` targets invoked, in the fixed
order protoc's own template always uses), and — even when the enum class and
its field names are renamed by R8 — the streaming kind, read from
`io.grpc.MethodDescriptor.MethodType`'s own compiler-embedded constructor
name argument (`"SERVER_STREAMING"`, etc.), which survives independently of
whatever the enum's class or field names become.

Verified against Mullvad's real, pinned `ManagementService` (94 RPCs,
including one server-streaming method, `EventsListen`): all 94 methods
recovered, request/response types and streaming kind exactly matching the
real source for 93/94; the one remaining "mismatch" is a naming-convention
difference, not a wrong recovery — the real nested type `Shadowsocks.
Ciphers` is referenced under this project's existing flattened-nesting
convention (`Shadowsocks_Ciphers`), the same convention every other nested
message reference in this codebase already uses. `ConnectTunnel`,
`DisconnectTunnel`, `GetTunnelState`, `SetRelaySettings`, and `EventsListen`
were spot-checked directly against the pinned source and match exactly,
streaming kind included.

| Metric | Result |
|---|---:|
| RPC methods recovered | 100.00% (94/94) |
| Exact request/response/streaming signature | 98.94% (93/94) |

The mechanism generalizes beyond the one app it was built against: rerunning
against Signal recovered two additional real gRPC services (`fog_view.
FogViewAPI`, `fog_key_image.FogKeyImageAPI`, MobileCoin/payments-related)
that no prior extraction path here surfaced at all.

Recovering this surfaced two real, separate bugs in the existing pipeline
that had nothing to do with gRPC itself, both now fixed:

- **Output file names could hard-fail a fully-recovered extraction.**
  Mullvad has two distinct DEX classes both named `Relay` in different
  packages (`mullvad_daemon.management_interface` and `mullvad_daemon.
  relay_selector`) — `reconcile()` has kept them correctly separate by
  `(package, name)` since an earlier session, but the output-writing step
  only ever checked the bare file name, so writing this app failed outright
  with `output name collision: Relay.proto`. This is not a rare corner
  case: the same collision was already latent in Molly's recovered output
  too, just not yet hit. The output step now disambiguates a colliding name
  by prefixing the owning package (`mullvad_daemon.relay_selector.Relay.
  proto`) and falls back to a numeric suffix when no package distinguishes
  it, rather than failing.
- **A single mis-flagged class could split its own package's file in two.**
  `newMessageInfo`'s per-class syntax bit occasionally disagrees with the
  real file-level syntax for one class in an otherwise-uniform package;
  combining recovered classes by `(package, syntax)` let that one
  disagreement split the package into two separate emitted files, each
  missing the other's real declarations and silently synthesizing an empty
  placeholder for what its sibling had already declared correctly. This
  degraded Mullvad's field recall from the documented 100% (297/297) to
  68.35% (203/297) and structural fidelity to 85.45% (47/55) — a real
  regression that had been sitting undetected because the file-name
  collision above had been failing the same extraction outright before
  reaching this step. Grouping by package alone (majority-voting the
  group's overall syntax) restores the documented 100% across every
  metric, confirmed by rerunning the full pbtk differential below.

Bitwarden (104 files), Smartspacer (70 files), and Molly (589 files) were
rerun after both fixes and are byte-for-byte unchanged in output count from
their previously published numbers; Signal gained the two gRPC service
files described above and is otherwise unchanged.

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
| Type fidelity | 100.00% | 100.00% |
| Name recovery | 100.00% | 100.00% |
| Label accuracy | 100.00% | 100.00% |
| Structural fidelity | 100.00% | 100.00% |
| Enum recovery | 100.00% | 100.00% |
| Compile rate | 100.00% | 100.00% |

ProtoLoom now matches pbtk exactly on **all nine rows** of this differential.
At the start of this document's history ProtoLoom lost on every row; the gap
closed one diagnosed root cause at a time — DEX `EnclosingClass` recovery,
proto3 synthetic-oneof recovery, declared-field-type resolution,
well-known-type imports, a schema-identity collision fix, and finally the
`*_FIELD_NUMBER` static-constant signal for names — with each fix's
before/after numbers recorded in this file and in the commit that made it.
On this unobfuscated real app, ProtoLoom no longer loses to pbtk at all; it
matches it exactly, while additionally reporting per-field confidence and
evidence that pbtk does not. The honest caveat is scope, not accuracy on this
target: this is one app, one ground-truth file, unobfuscated Java-lite —
Bitwarden and Smartspacer confirm several of these fixes generalize, but
Other corpus rows still expose separately documented optimizer and information
limits; this one-app differential does not characterize them. The pinned
adapter is
`scripts/pbtk_1_1_2_adapter.sh`; `scripts/compare_pbtk.sh` records isolated
tool logs and statuses for a directory of artifacts.

## Extraction performance

Profiling a full `protoloom extract` run against the pinned Signal APK
(cProfile, wall clock) showed 28 of its 56 measured seconds inside
`compile_proto` — 830 separate real `protoc` subprocess spawns, each
individually validating one recovered schema compiles standalone before it
is written out, or one per-package group before its combined descriptor set
is assembled. Each call is a real, independent OS process whose Python-side
wait (`subprocess.Popen.wait`) releases the GIL, so the calls were being
serialized for no reason: nothing about validating schema N depends on
having already validated schema N-1. `_compiled_descriptors_many` now runs
these through a `ThreadPoolExecutor` (capped at 32 workers) instead of a
plain loop, in both call sites (`cli.py`'s per-schema pre-write validation
and `_combined_lite_descriptors`'s per-package group compile), preserving
submission order so output ordering and first-failure error attribution are
unchanged.

| APK | Files recovered | Wall time before | Wall time after |
|---|---:|---:|---:|
| Signal 8.22.2 | 789 | 56.1s | 30.4s |
| Mullvad 2026.8 | 130 | — | 29.1s |
| Molly 8.19.2-4 | 589 | — | 18.8s |
| Smartspacer 1.11.2 | 70 | — | 7.3s |

Signal's output directory is byte-for-byte identical before and after
(`diff -rq`, zero differences across all 789 files); the other three
reproduce their documented file counts and zero bail-outs unchanged. This is
a pure concurrency change — no extraction, decoding, or emission logic was
touched — verified by the fact that the one app measured both before and
after did not move by a single byte.

A second, independent redundancy in the same command: `_find_lite`,
`_find_wire`, and `_find_grpc` each built their own `DexFile(data)` from the
same raw dex bytes, so every input dex in an APK was fully parsed three
times over — real work, not a subprocess wait, so it doesn't overlap with
the thread-pool fix above. `_cached_dex` now shares one `dict[str, DexFile]`
across all three finders (populated lazily by whichever finder runs first),
so each dex is parsed exactly once per extraction.

| APK | Wall time (thread pool only) | Wall time (+ shared DexFile parse) |
|---|---:|---:|
| Signal 8.22.2 | 30.4s | 27.2s |

Signal's output stayed byte-for-byte identical after this change too, and
Mullvad (130 files), Molly (589 files), and Smartspacer (70 files) all
reproduced their documented counts unchanged, each verified with `diff -rq`
against their own prior output.

A third redundancy of the same shape: `detect(path)` and, for archive
inputs, `AndroidArchive(path).inventory()` were each still being called
multiple times per single `extract` invocation, once inside `_dex_inputs`,
once inside `_find`, once inside `_find_go_tags`, and once more for the
`--jadx` flag check — each call re-opens the file and, for a zip-shaped
container, re-parses the entire central directory. `extract()` now computes
`detection` and (when relevant) `inventory` exactly once and threads them
through all four call sites. This one measured near the noise floor on
Signal (27.2s before and after, i.e. under ~1s), confirming this redundancy
was real but small next to the two fixes above; still worth fixing, since
it's the same bug shape and the file no longer carries dead re-parsing.
Verified byte-identical against Signal, Mullvad, Molly, and Smartspacer's
prior outputs.

A fourth attempt — parallelizing `_find_lite`'s remaining genuine per-dex
CPU work (`iter_code_items`/`extract_lite`/`decode_lite_finding`) across a
`ProcessPoolExecutor` — was implemented, initially measured as a real
speedup (Signal 26.1s → 19.3s) with byte-identical output on a handful of
runs, and merged, but a stress run of 5 back-to-back real Signal
extractions immediately afterward failed 4 of 5 times with three different
real errors (`BrokenProcessPool`, a `DEX structure lies outside the file`
decode error, and a `TypeError` on an internal object). Switching the pool's
start method from `fork` to `spawn` had reduced but not eliminated the
failure rate — the small number of clean verification runs during
development simply hadn't hit it. **Reverted in full** rather than shipped
in a known-flaky state; a slower, reliable extractor is worth more than a
faster, unreliable one. The underlying idea (real CPU-bound work that would
benefit from process-level parallelism) is still correct — it needs a
correctness root-cause first, not a smaller tweak to the same design, before
it's attempted again.

## Cross-corpus robustness check

Beyond the 4 apps re-verified repeatedly during the performance and
coverage work above, all 8 apps pinned in `benchmarks/corpus/
tier-b-real-apps.json` were fetched (hash-verified) and extracted together
in one pass: `bitwarden-authenticator`, `flipper`, `gadgetbridge`,
`meshtastic`, `molly`, `mullvad`, `signal`, `smartspacer`. Every one
reproduced its documented file count exactly with zero bail-outs
(104/61/646/289/589/130/789/70), spanning 1 dex file (flipper, mullvad) to
8 (signal), with gRPC service recovery correctly firing only on the two
apps that actually declare gRPC services (mullvad, signal).

Also run against two APKs with no relationship to this project's corpus or
to protobuf at all — the Godot 4.3 Android export templates (a C++/
GDExtension game engine, nothing like the Java/Kotlin toolchains this tool
targets) — plus an empty file and 10KB of random bytes. All four exited
cleanly with `no recoverable schema evidence found` (exit 2), no crash, no
hang.

One real gap surfaced: with no `protoc` binary on PATH (true for this
environment, and for any real install that hasn't specifically added one),
`protoloom extract` silently falls back to the bundled `grpc_tools.protoc`
(v35.1) rather than this project's pinned v29.3. Confirmed this produces
byte-identical output across the full 8-app corpus either way — not a
correctness bug — but `protoloom doctor` reported this state as the bare,
uninformative string `"python"`. Fixed to report
`"python -m grpc_tools.protoc (not the pinned protoc binary)"` so the
actual compiler in play is no longer hidden from anyone debugging a
compile discrepancy.

## Test coverage hardening

Following the four extraction-performance fixes above, a wide coverage pass
closed nearly every remaining real gap in the engine, mostly via
worktree-isolated subagents (each given real-bytecode fixture conventions
to follow, no padding, `make check` gating) merged back and independently
re-verified — real-APK output was diffed byte-for-byte against the last
verified state after every merge; none of this changed production
behavior.

| Module | Before | After |
|---|---:|---:|
| `container/dex.py` | 73% | 100% |
| `container/apk.py` | 94% | 100% |
| `container/detect.py` | 89% | 100% |
| `container/elf.py` | 87% | 100% |
| `container/macho.py` | 90% | 100% |
| `extract/wire.py` | 56% | 99% |
| `extract/lite.py` | 89% | 99% |
| `extract/grpc.py` | 93% | 100% |
| `extract/gotags.py` | 84% | 100% |
| `extract/descriptor.py` | 88% | 100% |
| `decode/wire.py` | 91% | 99% |
| `decode/lite.py` | 82% | 100% |
| `decode/names.py` | 89% | 100% |
| `decode/infostring.py` | 94% | 100% |
| `reconcile.py` | 90% | 100% |
| `emit/report.py` | 93% | 99% |
| `emit/dashboard.py` | 94% | 100% |
| `cli.py` | 89% | 93% |
| **Overall (6211 statements)** | **87%** | **97%** |

Test count rose from 730 to 961 across this pass. The handful of lines left
uncovered were checked individually and found genuinely unreachable rather
than skipped by default: `decode/wire.py`'s `raw_type not in dex.types`
guard (the value is always extracted from that same list, so membership is
trivially guaranteed), `emit/report.py`'s outer message-count budget check
(the per-schema helper it follows already enforces a shrinking sub-budget
that makes this exact overflow impossible by construction), and 4 lines in
`extract/lite.py`'s packed-switch bounds validation (only a corrupted,
backward payload delta reaches them, and real D8/R8 output never produces
one — confirmed experimentally). Remaining gaps outside this pass
(`bench/*.py`, `extract/jadx.py`, `tui/*`, `validate/roundtrip.py`) sit
outside the core recovery engine — CLI-adjacent, benchmark-harness, or
interactive-TUI code — and were left for a future session.

### Follow-up sweep

A later pass closed the remaining core-engine gaps
(`emit/jsonout.py`, `extract/gozip.py`, `container/read.py`, `doctor.py`,
`validate/compile.py`, `emit/descset.py` — all real TOCTOU/budget/fallback
edges, no contrived tests) and the CLI-adjacent gaps left above
(`bench/*.py`, `extract/jadx.py`, `tui/*`, `validate/roundtrip.py`), taking
the suite from 961 to 1060 tests. Two real bugs surfaced along the way, both
fixed and verified against the full Signal APK re-extraction:

- **TUI**: pressing Escape on the Setup/Open/Results screens while a text
  input had focus never returned to Home — the generic "go back" escape
  binding was the only screen-transition escape handler in the file without
  `eager=True`, so prompt_toolkit's own buffer key handling won the race.
  Fixed with `eager=True`.
- **Emit**: a recovered proto3 enum whose first value isn't 0 but that has
  some *other* value literally equal to 0 collided with the synthesized
  `_UNSPECIFIED = 0` placeholder without `allow_alias` being set, producing
  a proto that fails to compile. Fixed by checking the synthetic zero
  against the real values before deciding whether `allow_alias` is needed.
  Signal's own schemas never hit this shape (`diff -rq` against the prior
  commit's extraction is empty), so this closes a real gap with zero
  behavior change on the pinned corpus.

## Adversarial black-box campaign ("/goal" testing loops)

A subsequent multi-round campaign treated protoloom purely as a black-box
CLI user would, deliberately hunting for false negatives/positives, crashes,
silent data loss, and UX gaps across real APKs, real system binaries, real
Go/JVM toolchains, and adversarial synthetic inputs — not unit tests written
to prove existing behavior correct. Ran 7 rounds of parallel subagents, each
independently choosing targets, reproducing anything surprising, root-causing
it, and fixing generally rather than special-casing. 18 real bugs found and
fixed, all verified with a full `uv run make check` and a byte-for-byte
`diff -rq` against the pinned real-app corpus before merging (confidence-
marking fixes intentionally changed specific `high`->`medium` markings on
Bitwarden's known-affected fields; every other fix was a true no-op on the
corpus, proving no regression). Test count: 1061 -> 1087.

**Bugs found and fixed, by area:**
- **Go struct-tag reflection** (`extract/gotags.py`): missing `int32`/`int64`
  scalar-kind table entries (every plain int field failed); `float`/`double`
  used the wrong Go `reflect.Kind` constants (off by one).
- **Raw descriptor scanning** (`extract/descriptor.py`): a crash when `upb`
  returns raw `bytes` instead of `str` for an invalid-UTF-8 name field (hit
  on a real 219MB Electron binary); dedup keyed on name alone silently
  dropped a second, genuinely different descriptor sharing a name (e.g. two
  APK modules bundling different dependency versions) instead of surfacing
  the conflict; a crash on real protobuf-*editions* syntax descriptors,
  which the scanner accepted but the model layer didn't support.
- **Gzip-embedded descriptors** (`extract/gozip.py`): only unwrapped one
  level of nesting; a bundler wrapping an already-gzipped Go descriptor a
  second time was silently missed entirely (0 findings, not an error).
  Added bounded recursion sharing the existing inflation budget.
- **CLI orchestration** (`cli.py`): plain `.jar` files with an embedded
  `classes.dex` were never scanned for dex-based (lite/wire/grpc) recovery,
  only for raw descriptors; `doctor` always exited 0 even with `protoc`
  missing, defeating its use in scripts/CI; Go-compiled `.so` libraries
  bundled *inside* an APK/AAB/JAR were never scanned at all (only a bare
  standalone ELF input triggered Go-tag reflection) — real apps bundling a
  Go-based SDK alongside Java/Kotlin code got zero native-side recovery;
  "certain" (whole, byte-scanned) descriptors were never compile-validated,
  so a descriptor referencing a type that was never actually found could be
  written out as a "recovered" `.proto` that doesn't compile, presented as
  success.
- **Dex parsing performance** (`container/dex.py`): `iter_code_items()` fully
  re-parsed the whole class/method/code-item table on every call, uncached;
  enum-recovery helpers call it once per field lookup across hundreds of
  schemas. Memoized — ~28% faster full extraction on signal-android
  (27.3s -> 19.7s), byte-identical output.
- **Decode layer** (`decode/descpb.py`): proto2 `TYPE_GROUP` fields were
  silently decoded/emitted as ordinary message fields — wire-incompatible
  with the original (group uses start/end-group framing, not
  length-delimited) — now round-trips correctly; no size/depth budget of
  its own, so a huge descriptor was fully decoded into Python objects
  before the emit layer's budget ever got a chance to reject it cheaply,
  and a descriptor with a field number in the reserved 19000-19999 range
  crashed the CLI with a raw traceback instead of a clean bail-out;
  `extension`/`extension_range` fields were silently dropped with no
  diagnostic (confirmed no app in the pinned corpus is actually affected
  today, by checking every pinned app's real upstream `.proto` source —
  but the silent-loss gap was real and is now flagged as a bail-out).
- **Confidence-marking honesty**: an unresolved enum field falling back to
  a lossy `int32` substitution kept `Confidence.HIGH` instead of dropping
  to MEDIUM, overselling confidence for what's really a guess (10 real
  fields on Bitwarden Authenticator affected); the dashboard HTML silently
  dropped the `kept_confidence`/`rejected_confidence` detail that
  `reconcile()` already computed for each conflict, when rendering the
  conflict list, making the dashboard less actionable than the underlying
  data.
- **Container detection** (`container/detect.py`): front-magic-only
  detection meant a ZIP with bytes prepended (self-extracting stubs,
  "reverse-signed"/polyglot APKs) was misclassified `UNKNOWN` even though
  Python's own `zipfile` opens it fine via the trailing EOCD record; fixed
  to fall back to a real zip-open attempt on `UNKNOWN`, while preserving
  the existing safety property that front-magic still wins when a
  non-zip file has a zip glued onto its end (a known malware-packer shape).

**Confirmed clean (well-tested already, no exploitable gap found):**
repeated/deterministic runs (byte-identical across 3-5x re-runs, CPU
affinity changes, thread-pool ordering), SIGINT/interrupted-run atomicity
(fully transactional publish with rollback), stale-artifact cleanup on
output-dir reuse, exit-code consistency, real cross-version schema diffing
(manual directory-diff workflow works well since output is deterministic),
gRPC service recovery correctness (94/94 real RPCs on Mullvad, including
streaming-direction detection), the interactive TUI live-driven via a real
pty (extraction flow, bad-path handling, resize, Ctrl-C cancellation),
reconcile's conflict-detail generation, emit-layer identifier sanitization
against real obfuscated (R8/ProGuard) names and adversarial synthetic
collisions, real bundletool-shaped `.aab` split-module dex discovery, and
`--jadx`'s subprocess lifecycle (timeout/kill/cleanup) — though `--jadx`
itself was found to never feed back into recovered schemas at all (a
README wording fix, not a functional bug, since decompiled-source-only
output is the documented/intended behavior once corrected).

**Left intentionally unsupported / genuinely untestable here:** proto2
`extend` blocks (flagged with an honest bail-out rather than full
support — no real pinned app needs it today); JVM-bytecode (non-dex)
protobuf-lite/Wire/gRPC decoding for plain `.jar`s (only the raw
descriptor-bytes path recovers anything from JVM bytecode; a full
JVM-bytecode decoder would be a new capability, not a bugfix); Flutter
(`libapp.so`/Dart) and React Native artifact handling (no Flutter/RN
toolchain available in this environment and no sample obtained — untested,
not claimed to work); real jadx decompilation quality (jadx itself isn't
installed here; only protoloom's side of the integration was verified).

## Extended adversarial campaign ("perfect foundation" pass)

A follow-up campaign (12 more rounds beyond the first 7) went deeper into
packaging/install, multidex/R8/Kotlin realism, APK signing blocks, memory
pressure, and — most significantly — byte-level accuracy verification
against every pinned app's real upstream `.proto` source at its pinned
commit, plus property-based (hypothesis) fuzzing of both the emit/compile
round-trip and the decode boundary. 18 more real bugs found and fixed
(36 total across the full campaign), taking the suite from 1087 to 1120
tests. Every production-code fix was verified with a full `make check` and
a byte-for-byte `diff -rq` (with an explicit field-number-preservation
check once fixes started concentrating in this area) against the pinned
8-app real corpus before merging.

**Most significant finding**: a category-confusion bug where an unresolved
type reference (in Wire-decoded, protobuf-lite-decoded, and — dormant but
present — descriptor-decoded schemas) was stubbed as an empty `message X {}`
placeholder even when X was actually an enum, which is a wire-format
category error (enum is varint-encoded, message is length-delimited) — the
recovered schema would be wire-incompatible with the real data. Discovered
via the upstream byte-level accuracy audit (Signal's real `AccessControl.
AccessRequired`), root-caused, and fixed in stages as its real scope kept
turning out broader than first measured:
1. First fix, scoped to Wire's stub synthesis: ~40 stub types across ~30
   files in the pinned corpus.
2. That fix's own enum-candidate heuristic was too broad (any class ever
   referenced as an ADAPTER owner, not just enum-shaped classes) and
   wrongly reclassified genuine message types (Meshtastic's
   `ChannelSettings`, several Gadgetbridge nested types) as enums — a real
   regression, caught by continuing the same verification discipline
   rather than assuming a fix that passed `make check` was correct, and
   fixed by splitting the reliable `getValue()->int` shape signal from the
   broader post-verified candidate set.
3. Extended to protobuf-lite decoding (`decode/lite.py` never set the
   enum/message hint at all) and to a dormant descpb.py gap (real protoc
   output is always fully-qualified so it never fires today, but a
   malformed/adversarial descriptor could have hit it).
4. Extended again to cross-dex references: a real multi-dex APK (Signal
   splits into 8 `classesN.dex` files) can define an enum in one dex while
   a field referencing it lives in another; the heuristic only scanned the
   one dex it was given.
5. A field referencing its own message's nested type by bare (non-absolute)
   name was incorrectly treated as an unresolved cross-file reference,
   producing a spurious duplicate top-level stub alongside the correct
   nested declaration — fixed by implementing real enclosing-scope
   resolution (searching the referencing message's own scope outward, the
   way protoc's own symbol resolution works) instead of a flat file-level
   lookup.

An **exhaustive audit** (not a sample) of every empty `message X {}` stub
across the full corpus was run twice — once mid-fix (2504 stubs, 3 real
bugs found) and once on the fully-fixed result (2426 stubs remaining, all
16 automated "possible category confusion" flags manually verified as
false positives — coincidental bare-name collisions between genuinely
unrelated types, e.g. Gadgetbridge's own `Label` message vs. protobuf's
well-known `FieldDescriptorProto.Label` enum). Zero remaining category-
confusion bugs found in the final pass.

**Other real bugs found and fixed this pass:**
- Two serious bugs surfaced by property-based fuzzing of `emit_proto`:
  a dotted raw name silently colliding with an unrelated nested type path
  (a field could end up referencing the wrong sibling type, still
  compiling — a silent-wrong-output bug, the most serious class this
  entire campaign found); and a keyword-named type reference (`message`,
  `enum`, etc. are legal declaration names but parse-fail as a bare field
  type) needing forced absolute qualification.
- A camelCase field-naming loss (`avgcadence` instead of `avg_cadence`)
  fixed by recovering the real accessor method name (`getAvgCadence`)
  R8 leaves unobfuscated even when it strips the objects-array name string
  the primary recovery path relies on.
- An enum value case-fold collision (protoc's C++ codegen rejects two enum
  values that collide after case-folding and prefix-stripping, a rule
  `_unique_names`'s exact-string dedup didn't model) — verified reachable
  on real data, not just synthetic.
- Two crashes/hangs under real memory pressure (`ulimit -v`): an
  over-large read buffer sized to the 256MiB max rather than the actual
  file size, and an unhandled thread-pool-refusal/`MemoryError` producing a
  raw traceback instead of a clean bail-out.
- A `.aar` (Android library archive, distinct from `.aab`) silently
  recovering nothing at all — container detection had no AAR case, so a
  descriptor sitting in a nested, compressed `classes.jar` (a real,
  common AGP output shape) was invisible to the raw byte scanner, which
  only ever scanned the outer file as one blob.

**Confirmed clean, checked with real evidence, no fix needed**: fresh-venv
install without dev extras, Python 3.11/3.12/3.13 compatibility, using
protoloom as a library (not just a CLI) — the import-linter contracts make
this a real, enforced boundary, not an accident; subprocess/shell-injection
hygiene (argument lists throughout, verified with maliciously-named real
files); CLI help-text accuracy; real multidex (up to 8 dex files) cross-dex
attribution and nesting; R8 full-mode obfuscated recovery; Kotlin-coroutine
gRPC stubs (structurally immune, since coroutine wrapping lives in a
different generated class the extractor never touches); real APK Signing
Block v2/v3 layout (already handled by the earlier zip-behind-stub fix);
`ulimit -n`/disk-full/locale/`PYTHONHASHSEED` sensitivity; the `bench`
subcommand's own input robustness; non-ELF (PE/Mach-O) Go cross-compiles
correctly declining rather than crashing; CI running the identical
`make check` a contributor runs locally, gated correctly before release.

**Left intentionally unsupported / genuinely untestable here**: Flutter and
React Native artifacts (no toolchain available in this environment, no
sample obtained — not claimed to work); a full JVM-bytecode (non-dex)
protobuf-lite/Wire/gRPC decoder for `.jar`/`.aar` contents (only the raw
descriptor-bytes path recovers anything there; a full decoder is a new
capability, not a bugfix); real jadx decompilation quality (jadx isn't
installed in this environment; only protoloom's side of the subprocess
integration was verified); a small number of narrow, already-safely-failing
edge cases (map fields in oneofs, a nested-scope keyword-qualification
corner) that were investigated and found to fail cleanly rather than
silently, and are documented rather than fixed given their inability to
produce wrong output.
