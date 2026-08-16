# Decoding protobuf-lite info strings

The schema looked gone. The generated Android class had no descriptor blob, no
`.proto` filename, and no useful symbols beyond the protobuf runtime call. It
still had this:

```text
newMessageInfo(DEFAULT_INSTANCE, info, objects)
```

That call is enough to recover structure, but only if all three operands are
read together. The `info` string is not text. Its UTF-16 code units are a stream
of packed integers. The objects array supplies the Java field names, message
classes, enum verifiers, map defaults, oneof storage, and presence bitfields
that those integers refer to implicitly.

## The format source is executable code

PROTOLOOM follows protobuf `v33.6` at commit
`6e1998413a5bca7c058b85999667893f167434bc`. `RawMessageInfo.java` explains the
layout and `MessageSchema.newSchemaForRawMessageInfo` is the final authority on
how it is consumed.

One detail shows why screenshots and remembered blog posts are bad format
specifications. The prose describes continuation code units around the
non-surrogate range, while the Java decoder's actual condition is `>= 0xD800`.
The implementation and boundary tests follow the executable condition.

The stream starts with flags and a field count. A non-empty message then carries
oneof and hasbit counts, minimum and maximum field numbers, allocation counts,
and map/repeated/check-initialized counts. Each field supplies its number and a
type ID with flag bits. Singular presence fields consume a hasbit index; oneof
types are scalar type IDs offset by 51 and consume a oneof index.

## Why wire type is the honest headline

The compact type table preserves enough information to distinguish `int32`,
`sint32`, `uint32`, packed lists, groups, maps, and the other protobuf field
families. That makes wire-type recovery exact when the call site survives.
Names are a different story: they arrive through Java field references and are
precisely the material an obfuscator wants to rewrite.

The decoder therefore returns raw flags and IDs before any schema emitter gets
involved. Invalid counts, duplicate field numbers, truncated continuation
integers, impossible oneof indexes, and trailing data are errors. A malformed
candidate does not become a plausible-looking schema.

## Recovering operands without a decompiler

DEX gives us a small, bounded dataflow problem. Starting from an exact
`GeneratedMessageLite.newMessageInfo` method reference, the extractor tracks the
registers used by its `invoke-static` call. It understands the constant and
object-array instructions generated for this block: strings, classes, integers,
object moves, static object references, `new-array`, and `aput-object`.

It is not a general Dalvik interpreter. Branches reset the tracked state. Calls
outside the target reset it too. An incomplete array, a non-constant info
string, or an unsupported construction produces a reasoned bailout instead of
a lower-quality schema. The bailout count is part of the benchmark result.

That choice makes failures less exciting and much more useful. Each new opcode
will be added because a pinned corpus artifact demonstrated the need, with that
artifact becoming the regression test.

## What works now and what remains

The unit corpus covers integer boundaries, all 69 runtime field IDs, scalar and
oneof presence, packed fields, both invocation encodings, ordered string/class
objects, empty messages, and malformed constructions. The next evidence is the
real-app corpus. Until those runs are published, this is a tested decoder, not a
claim of 95 percent real-world accuracy.
