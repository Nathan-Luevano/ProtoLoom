# Protobuf-lite format pin

The decoding core follows protobuf release `v33.6`, commit
`6e1998413a5bca7c058b85999667893f167434bc`.

The sources read for this implementation are pinned here:

- [RawMessageInfo.java](https://github.com/protocolbuffers/protobuf/blob/6e1998413a5bca7c058b85999667893f167434bc/java/core/src/main/java/com/google/protobuf/RawMessageInfo.java)
- [MessageSchema.java](https://github.com/protocolbuffers/protobuf/blob/6e1998413a5bca7c058b85999667893f167434bc/java/core/src/main/java/com/google/protobuf/MessageSchema.java)
- [GeneratedMessageLite.java](https://github.com/protocolbuffers/protobuf/blob/6e1998413a5bca7c058b85999667893f167434bc/java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java)
- [MessageInfo.java](https://github.com/protocolbuffers/protobuf/blob/6e1998413a5bca7c058b85999667893f167434bc/java/core/src/main/java/com/google/protobuf/MessageInfo.java)
- [FieldType.java](https://github.com/protocolbuffers/protobuf/blob/6e1998413a5bca7c058b85999667893f167434bc/java/core/src/main/java/com/google/protobuf/FieldType.java)

Use the commit-pinned GitHub URLs when checking behavior. In particular, the
runtime treats every UTF-16 code unit at or above `0xD800` as a continuation,
and oneof type IDs are scalar IDs plus 51. The objects array is consumed in
parallel with the integer stream; it is not an auxiliary index encoded into
every field entry.
