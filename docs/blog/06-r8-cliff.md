# What R8 actually destroys

R8 does not have one protobuf failure mode. It can rename the evidence, reshape
the code carrying it, or remove the recognizable call pattern entirely. Those
outcomes need separate numbers.

The easiest case leaves `newMessageInfo` and its packed type stream intact but
renames backing fields to `a_`, `b_`, and friends. Field numbers and wire types
remain recoverable. Semantic names do not. PROTOLOOM calls a message obfuscated
when a strict majority of its recovered Java names match one- or two-letter
identifiers, then emits numbered placeholders at speculative confidence. It
does not present `a` as recovered truth.

The harder case changes the objects-array construction enough that the bounded
DEX interpreter cannot resolve every element. That is a counted dataflow
bailout. The worst case inlines or transforms the metadata call so the exact
runtime method reference disappears; that is coverage loss, not a decoding
error. Reporting all three as “R8 failed” would hide the useful part of the
result.

## A corpus that can be rerun

The real-app manifest begins with Signal Android, Molly, and Mullvad. Each entry
ties an official GitHub release APK to an immutable source commit containing
public `.proto` files. GitHub's published asset digest, byte size, source tree,
license, and selected schema paths are recorded. APK bytes stay outside the
repository.

The fetcher accepts HTTPS only, downloads to a temporary file, caps the stream
at the pinned size, verifies SHA-256, and atomically installs a match. It refuses
to overwrite a mismatch. These controls make the input reproducible; they do
not make an APK safe to execute.

## The table is deliberately not here yet

No extraction run has produced a defensible R8-cliff number in this milestone,
so this draft does not invent one. The final table will separate:

- call-site coverage;
- dataflow bailout rate and reason;
- field and wire-type recovery among resolved calls;
- exact-name recovery before and after the obfuscation detector;
- byte-identical payload round trips.

It will also name the worst target and keep it in the table. If an app's public
schemas do not correspond to the code actually packaged in its release APK,
that mismatch will be reported rather than silently removed from the sample.

The interesting question is not whether R8 hurts recovery. It is where the
cliff begins, which evidence survives after the fall, and whether that boundary
is stable across real build pipelines. The pinned manifest makes that question
answerable without redistributing anybody else's application.
