# Building the scoreboard before the thing it scores

A schema recovery tool can produce a file that looks excellent and is wrong in
every way that matters. Field names are persuasive. Clean indentation is
persuasive. Neither tells you whether the result decodes a payload.

That made the order of work unusually important for PROTOLOOM. The protobuf-lite
extractor is the difficult part and the part I expect to tune repeatedly. If I
defined its benchmark after seeing its failures, every metric decision would be
suspect. So I built the scoreboard first.

The smallest unit of comparison is a field number inside a matched message.
Fully-qualified message names win when they survive. When they do not, the
harness falls back to a structural signature made from field numbers and wire
types, but only when that signature selects one message. Ambiguity is a miss.
Guessing would make the chart prettier and the tool less honest.

Field recall and precision answer different questions. Recall catches evidence
the extractor missed. Precision catches fields it invented. Wire-type accuracy
asks whether the recovered declaration can consume the same bytes. Exact type
fidelity is stricter: a field can have the correct varint wire type and still be
wrong about `int32`, `sint32`, or `uint32`. Name and label accuracy are separate,
because R8 may destroy identifiers while leaving repeated-field structure
intact.

Structure needs its own score too. The harness treats each nesting relationship
and each oneof membership set as a ground-truth group. Enum recovery counts
exact name-and-number pairs. Compilation is a binary gate: if the emitted schema
does not compile, every score for that target is zero. A non-compiling file is
not partially operational.

Round trips are the hardest check to bluff. Given a captured payload, decode it
with the recovered schema and re-encode it. The bytes must be identical. This is
not proof about fields the payload never exercises, but it is strong evidence
about the ones it does. The harness records passed payloads over attempted
payloads rather than turning one convenient example into a success claim.

Aggregation is another place results can become accidentally flattering.
Micro scores pool every numerator and denominator, so a huge easy schema can
hide a small disaster. Macro scores give each target one vote, so a tiny oddball
can have more influence than its field count suggests. PROTOLOOM reports both
and explicitly labels the lower one as the lead value. Per-target output remains
available because an average without its worst member is not a debugging tool.

The corpus manifest is deliberately dull JSON. Every artifact has a SHA-256 and
exactly one local path or URL. Downloads go through a temporary file, hashes are
checked before use, and a mismatch deletes the bad output. The manifest also
expands a deterministic compilation matrix. Today the tiny calibration corpus
uses two runtimes and two optimization settings; the real Tier A matrix will add
protobuf versions, Android packaging, R8 modes, architectures, and native
optimization levels without changing the scoring code.

The initial target is synthetic in the narrowest possible sense: I hand-built
the expected mistakes. It has five ground-truth fields, one hallucinated field,
two exact-type mistakes with correct wire types, one damaged name, half an enum,
and one successful round trip out of two. The harness reports 100% recall,
83.33% precision, 100% wire accuracy, 60% type fidelity, 80% name recovery, 50%
enum recovery, and 50% round trips. Those are calibration values, not PROTOLOOM
accuracy numbers.

Ceilings are represented explicitly as counts too. For a set of types that the
available evidence cannot distinguish, the best corpus-level strategy chooses
the most common member of that ambiguity group. Everything outside the group is
identifiable. Reporting that ceiling matters: otherwise an information limit
looks like an implementation bug, or an implementation bug gets excused as an
information limit.

What is still broken? The benchmark library is not wired into the Typer command,
and the local fixture is not the pinned Google API corpus. The Path 1 extractor
still needs to produce the recovered snapshot automatically, compilation must
run through `protoc`, and round trips must use generated payloads rather than a
declared count. Those are integration tasks, and the baseline table labels them
plainly. The important constraint is now in place: when protobuf-lite recovery
arrives, it must face a measuring stick that was built without looking at its
score.

