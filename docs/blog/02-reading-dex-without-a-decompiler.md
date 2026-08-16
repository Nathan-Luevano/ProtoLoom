# Reading a DEX file without a decompiler

A protobuf recovery tool needs surprisingly little from Android bytecode. It
does not need reconstructed Java, pretty variable names, or control-flow graphs.
For the protobuf-lite path it needs the string pool, references to a small set of
methods, class membership, and the code units belonging to generated methods.

That changes the engineering tradeoff. A decompiler is an excellent interactive
reverse-engineering tool, but it is an expensive and unstable parser API.
Decompiler output changes with versions, optimized methods sometimes fail to
render, and every extraction now depends on a JVM subprocess. DEX itself is a
documented binary format with offsets and explicit table sizes.

PROTOLOOM's reader begins with the 112-byte header. It rejects inconsistent file
sizes and unsupported endian tags before following any offset. The identifier
tables then lead to `string_data_item`, type descriptors, methods, and class
definitions. Class data is variable-width: field and method indexes are stored
as ULEB128 deltas. Direct and virtual method lists each restart their delta base,
a small rule that produces convincing but wrong method references if missed.

Code items are deliberately left as 16-bit Dalvik code units. Recovery needs to
recognize calls and recover their operands; turning those instructions back into
Java would add complexity without adding evidence. Try tables and alignment are
range-checked so a damaged input fails locally instead of making a later scanner
interpret unrelated bytes as instructions.

Strings have their own trap. DEX uses modified UTF-8: NUL is encoded as the
two-byte sequence `c0 80`, and supplementary characters can appear as encoded
UTF-16 surrogate pairs. Comparing the decoded pool with androguard is therefore
part of the test plan. Synthetic fixtures cover bounds and edge cases, while a
real APK oracle catches assumptions that fixture authors share with the parser.

The current reader inventories DEX files directly and walks multidex members in
APK and AAB archives. It does not perform general dataflow analysis yet. That is
the next boundary: enough Dalvik semantics to recover `newMessageInfo` operands,
still without pretending to be a decompiler.
