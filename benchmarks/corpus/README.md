# Benchmark corpora

## Tier A upstream corpus

`tier-a-upstream-sources.json` pins four upstream source families by full commit,
SHA-256, and byte size. The corpus contains 13 selected roots from protobuf's
unittest and conformance trees, Google APIs, gRPC Health, and Envoy. Envoy's
archive contains repository symlinks, so its three self-contained schemas are
individually pinned instead of weakening the archive extractor's link refusal.

The driver downloads into a disposable cache, refuses non-HTTPS redirects,
oversized responses, hash or size mismatches, cache symlinks, archive links,
special files, and traversal paths. It compiles each selected root with its real
include tree, scans the serialized descriptor, emits a recovered proto, and
recompiles that output. Generated truth and recovery JSON files are ordinary
`protoloom bench` targets, not download counters.

```console
uv run python scripts/run_tier_a_upstream.py \
  --protoc /path/to/protoc \
  --cxx /path/to/c++ \
  --protobuf-include /path/to/protobuf/include
uv run protoloom bench --corpus tier-a-upstream --per-target
```

The driver also compiles generated C++ objects for three representative roots.
That leg is deliberately separate from the exact embedded-descriptor targets:
current protoc C++ objects do not contain a standalone descriptor encoding that
the byte scanner accepts, so those three results are recorded as negatives.

## Tier B real-app corpus

`tier-b-real-apps.json` contains metadata only. APKs are fetched from official
project releases or F-Droid and must never be added to this repository.

All eight entries were downloaded and byte-verified on 2026-08-16. Their
`sha256` and `size` values describe the downloaded release assets, and their
source commits are immutable schema trees resolved from release tags or an
application-pinned schema dependency:

- Signal Android `v8.22.2`
- Molly `v8.19.2-4`
- Mullvad Android `2026.8`
- Bitwarden Authenticator `v2026.7.1-bwa`
- Meshtastic Android `v2.8.1-internal.3`
- Flipper Android `1.8.1.1890`
- Gadgetbridge `0.93.0` from F-Droid
- Smartspacer `1.11.2`

Run the fetcher with a destination outside the checkout:

```console
uv run python scripts/fetch_real_apps.py \
  --destination /path/to/untrusted-protoloom-corpus \
  --app signal-android-8-22-2
```

The manifest selects a small set of public schemas for initial comparisons.
Signal and Molly intentionally remain a fork pair and count as one independent
schema family. The other six entries use independent schema sources, giving
seven independent families across eight shipping artifacts. A source tree
containing a schema does not by itself prove that the matching message was
packaged into the APK; extraction runs establish that and retain negative
results. Flipper is the current negative control: its selected schemas are in
the pinned source tree, but the direct lite extractor found no recoverable
evidence in the release APK.
