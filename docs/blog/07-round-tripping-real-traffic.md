# A protocol for validating captured traffic

A recovered schema can compile and still be wrong. `int32`, `uint32`, and
`sint32` are all integers in source, but they do not encode every value the same
way. Packed and unpacked repeated fields can decode into similar objects. An
incorrect nested message guess may look fine until a different payload arrives.

Round-trip validation supplies a harder verdict. Decode a captured payload with
the recovered descriptor, encode the resulting message again, and compare the
bytes. Byte identity proves that the schema explained that payload without
changing its observable representation. A mismatch turns the recovery into a
hypothesis that needs diagnosis.

It is not proof that the complete schema is correct. A capture may never exercise
an omitted optional field, oneof branch, enum value, or packed representation.
Unknown fields can also survive parsing and serialization in ways that mask a
missing declaration. The report therefore keeps round-trip rate beside field
recall and type fidelity for synthetic targets where ground truth exists.

The capture workflow is intentionally separate from extraction. Traffic may
come from mitmproxy, a Burp extension, an application test, or a saved fixture.
PROTOLOOM needs payload bytes and framing metadata, not privileged access to a
live device. That matters on WSL and air-gapped analysis systems where running
an emulator beside the tool is inconvenient or impossible.

The local calibration benchmark includes a deliberate failed round trip, and
the pinned matrix has one synthetic byte-identical round trip. Tier C now adds
one real Google Authenticator account-transfer payload created with a disposable
credential on an Android emulator. Both the pinned upstream Bitwarden schema and
ProtoLoom's recovered schema decode and semantically round-trip it.

The captured 63 bytes reserialize to 61 bytes because the app explicitly wrote
the default proto3 value `batch_index = 0`, while deterministic serialization
omits it. The benchmark records semantic success and byte-identity failure. That
distinction prevents a harmless canonicalization difference from being called
data loss without pretending the bytes were identical. The fixture retains the
binary payload and provenance, not the credential-bearing QR image.

The practical output is a binary descriptor set, not only a pretty `.proto`.
That artifact can immediately drive `protoc --decode`, `grpcurl`, or a proxy
addon. Recovery becomes useful when the same evidence that produced the schema
can explain traffic outside the extractor.
