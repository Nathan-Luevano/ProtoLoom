# Where PROTOLOOM is wrong

Schema recovery has an information ceiling. A stripped binary cannot reveal a
fact that its compiler and optimizer removed, and a recovery tool should not
hide that limit behind generated names.

PROTOLOOM's strongest path finds a validated `FileDescriptorProto`. When that
descriptor survives, the result is the producer's own schema. When it does not,
protobuf-lite metadata can retain field numbers, types, presence, oneofs, and
sometimes names. Obfuscation may preserve structure while destroying semantic
names. Native code with descriptors removed and payload-only inference remain
outside the initial high-confidence scope.

There are implementation failures too. A scanner can miss a descriptor split
by a linker. DEX dataflow can bail out on an unfamiliar objects-array
construction. A permissive parser can accept random bytes. Reconciliation can
choose between contradictory findings incorrectly. These are not fundamental
ceilings; each should appear as a failing corpus target or an explicit conflict.

The output carries confidence per field because uncertainty is uneven. A
validated descriptor byte range is `certain`. A decoded lite info string can be
`high`. A generated name after R8 should not inherit confidence from a reliable
field number. Evidence locations and rejected alternatives stay in the machine
readable report.

This is a draft of the failure post, not the final verdict. The Tier B real-app
table and differential pbtk run are not complete, so there is no defensible
"worst target" yet. The final version will lead with that target, publish macro
and micro scores, show bail-out counts, and separate unavailable ground truth
from a zero score. `scripts/compare_pbtk.sh` exists so disagreement can be
reproduced rather than narrated.

Known present limits are already concrete: no javanano or javamicro recovery,
no stripped-native code-pattern inference, no payload-only schema synthesis,
and no guarantee that an unobserved field can be recovered. A successful round
trip covers the captured bytes, not every payload the application could emit.

The goal is not a tool that claims perfection. It is a tool whose wrong answers
are measurable, traceable, and harder to mistake for facts.
