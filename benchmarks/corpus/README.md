# Real-app corpus

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
