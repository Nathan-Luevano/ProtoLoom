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
| Mullvad 2026.8 | 129 | 0 | recovered |
| Bitwarden Authenticator 2026.7.1 | 104 | 0 | recovered |
| Meshtastic 2.8.1-internal.3 | 52 | 1 | recovered; separately pinned schema repository |
| Flipper 1.8.1.1890 | 0 | 6 | no recoverable evidence |
| Gadgetbridge 0.93.0 | 646 | 0 | recovered |
| Smartspacer 1.11.2 | 70 | 0 | recovered with explicit enum uncertainty |

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
| Bitwarden Authenticator, `google_authenticator.proto` | 100% (12/12) | 100% (12/12) | 100% (12/12) | 75% (9/12) | 100% (12/12) | 0% (0/1) | 100% (5/5) | 100% (1/1) |
| Meshtastic, three selected files | 0% (0/483) | n/a (0 recovered) | n/a | n/a | n/a | 0% (0/132) | 0% (0/449) | 100% (3/3) |
| Flipper, three selected schema groups | 0% (0/36) | n/a (0 recovered) | n/a | n/a | n/a | 0% (0/2) | 0% (0/20) | 100% (3/3) |
| Gadgetbridge, three selected files | 100% (115/115) | 100% (115/115) | 100% (115/115) | 100% (115/115) | 63.48% (73/115) | 100% (16/16) | 100% (27/27) | 100% (3/3) |
| Smartspacer, `smartspace.proto` | 100% (37/37) | 100% (37/37) | 100% (37/37) | 86.49% (32/37) | 100% (37/37) | 100% (7/7) | 7.41% (2/27) | 100% (1/1) |

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
Signal, Molly, Gadgetbridge, and aggressively obfuscated builds still show
real, separate gaps (bail-outs, the R8-stripped structural cases) that this
session did not touch. The pinned adapter is
`scripts/pbtk_1_1_2_adapter.sh`; `scripts/compare_pbtk.sh` records isolated
tool logs and statuses for a directory of artifacts.
