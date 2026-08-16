# The schema is still in the binary

Sometimes protobuf recovery is not inference. The compiler has copied the
answer into the program.

Full protobuf runtimes need descriptors for reflection and registration. C++,
Go, Python, C#, and full Java commonly retain serialized
`FileDescriptorProto` messages in generated artifacts. Go often wraps its raw
descriptor in gzip. Symbols may be stripped and section names may be unhelpful,
but the bytes still describe the original file: package, messages, fields,
enums, dependencies, options, and source syntax.

Searching for any byte sequence that parses as protobuf is not enough.
Protobuf's wire format is permissive, so ordinary binary data produces false
positives. PROTOLOOM scans candidate length-delimited regions, parses them as a
descriptor, and applies structural validation. A credible file descriptor has
a valid name and field declarations whose numbers, labels, and types agree with
the descriptor contract. Findings are deduplicated by descriptor identity and
retain their exact source offset as evidence.

Container awareness makes this useful across targets. ELF sections and Mach-O
constant regions can be scanned without loading executable code. APK, AAB, and
JAR members are inventoried with bounded reads. Go build markers identify a
useful native target, while a gzip scanner handles `rawDesc` data without
assuming a symbol survived stripping.

When validation succeeds, field confidence is `certain`. That word is reserved
for data copied from the producer's own descriptor, not for a strong-looking
guess. The emitted `.proto` remains compile-validated and the binary descriptor
set can be loaded directly by tools such as `protoc` and `grpcurl`.

This path has a sharp ceiling. Lite runtimes deliberately avoid carrying full
descriptors, and native builds can remove reflection data. Compression and
linker transformations can also split or rewrite candidate regions. The honest
result in those cases is no finding, not a fabricated schema. Protobuf-lite
message information is the next recovery path, with lower confidence and a
different failure model.
