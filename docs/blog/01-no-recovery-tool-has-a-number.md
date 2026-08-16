# Every protobuf schema recovery tool, and why none of them have a number

I had a protobuf payload, the stripped Android app that produced it, and no
schema. The available recovery tools gave me output that looked plausible. What
none of them gave me was a reason to trust it.

That is a peculiar gap. Protobuf recovery is not new. pbtk searches application
artifacts and leans on decompilers. reprotobuf and androproto explore generated
Android code. Small scripts and one-off Gists scan binaries for descriptors.
Wire-oriented tools can infer a payload's shape without seeing the producer at
all. Each approach preserves a different subset of the original schema, and
each fails differently after optimization or obfuscation.

There is still no shared scoreboard. A tool can recover ten convincing schemas,
publish screenshots of the cleanest two, and never answer the questions that
matter: How many fields did it miss? How many did it invent? Did it preserve
the distinction between `int32`, `sint32`, and `uint32`? Can the result compile?
Can it decode and byte-identically re-encode a captured payload?

That last question is where plausible output becomes operational evidence. A
pretty `.proto` that changes bytes during a round trip is a hypothesis.

PROTOLOOM starts from the measurement problem. Its corpus will retain the
original schemas, build them through multiple protobuf and optimization
versions, strip the resulting artifacts, and compare recovered structure to the
known source automatically. The scorecard will report field recall and
precision, wire-type and exact-type accuracy, name recovery, compile rate, and
round-trip rate. Macro scores will keep one large schema from hiding failures on
small ones. Results will include failures, not just a highlight reel.

The recovery side has two initial paths. Some native, Go, and full-runtime
binaries preserve serialized `FileDescriptorProto` data; when found and
validated, that is the original schema and deserves `certain` confidence.
Modern protobuf-lite Android applications retain compact message-info strings
and object arrays. Those can preserve field numbers, types, oneofs, presence,
and sometimes names, but the evidence is less direct and R8 can damage it.

Every recovered field therefore carries both confidence and evidence. The tool
will say whether a claim came from a validated descriptor byte range or from a
particular class and info-string index. Uncertainty should survive all the way
to `recovery.json`, not disappear behind clean syntax.

The first milestone contains no extractor. It establishes the typed model,
architecture boundaries, reproducible environment, tests, and checks against
docstrings and attribution leakage. That is intentionally unglamorous. The next
step is a minimal DEX reader that gets exactly the information needed for
protobuf-lite without putting a decompiler in the hot path.

What is still broken? Almost everything users came for: no binary inspection,
no descriptor scan, no schema emission, and no benchmark result yet. The point
of publishing now is to make the evaluation contract visible before the numbers
exist. When the numbers are inconvenient, the contract cannot quietly change.

