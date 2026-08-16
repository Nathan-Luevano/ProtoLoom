# Real-app corpus

`tier-b-real-apps.json` contains metadata only. APKs are fetched from official
GitHub Releases and must never be added to this repository.

The three initial entries were checked on 2026-08-16 against the GitHub release
API. Their `sha256` values are the asset digests published by GitHub, and their
source commits are the immutable trees resolved from the corresponding release
tags:

- Signal Android `v8.22.2`
- Molly `v8.19.2-4`
- Mullvad Android `2026.8`

Run the fetcher with a destination outside the checkout:

```console
uv run python scripts/fetch_real_apps.py \
  --destination /path/to/untrusted-protoloom-corpus \
  --app signal-android-8-22-2
```

The manifest selects a small set of public schemas for initial comparisons. A
source tree containing a schema does not by itself prove that the matching
message was packaged into the APK; extraction runs must establish that and keep
negative results.
