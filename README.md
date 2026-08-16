# PROTOLOOM

Recovers compilable `.proto` schemas from stripped Android, native, and Go
binaries. Measured against real ground truth, not vibes.

PROTOLOOM currently recovers two kinds of evidence:

- embedded `FileDescriptorProto` data, including gzip-wrapped Go descriptors;
- protobuf-lite `newMessageInfo` metadata found through bounded DEX dataflow.

Every run records field-level confidence and evidence. Output includes
compilable `.proto` source, a binary descriptor set, `recovery.json`, a Markdown
report, and a self-contained HTML dashboard. The core path is deterministic and
does not use an LLM or require a decompiler.

This is alpha software. Path 1 has exact-descriptor unit coverage. The lite
decoder is pinned to protobuf v33.6 upstream source. A hash-pinned matrix across
protobuf-javalite 3.21.12, 4.29.3, and 4.35.1 plus default/aggressive R8 records
100% field recall, wire-type accuracy, compile rate, and byte-identical
round-trip rate on the local hostile schema. Real Mullvad ground truth records
90.91% field recall and 100% wire-type accuracy. The lower structural, enum,
and exact-type results are published rather than hidden.

## Quick start

```console
uv tool install .
protoloom inspect app.apk
protoloom extract app.apk --output out
protoloom extract app.apk --jadx --output out
protoloom demo
protoloom bench --corpus tier-a-small --per-target
protoloom doctor
```

The `extract` command scans APK/AAB DEX and native members as well as raw DEX,
ELF, Mach-O, PE, Go, and generic binary inputs. Unsupported lite bytecode is a
visible bail-out, never a silently guessed schema.

`--jadx` is an explicit Android-only fallback for retaining decompiled Java
context under `out/jadx`. It is never used unless requested, runs without a
shell, has a 120-second default timeout, and fails visibly when jadx is missing
or unsuccessful. Change the bound with `--jadx-timeout`.

## Validation

Run every local gate with:

```console
uv run make check
```

The suite enforces Ruff formatting and linting, strict mypy, zero docstrings,
layer boundaries, and the test suite. `protoloom demo` performs a complete
recovery and emits operational artifacts without downloading a corpus.

Real-app APKs are not redistributed. The Tier B manifest pins eight official
release artifacts across seven independent schema families with matching
immutable source revisions. `scripts/fetch_real_apps.py` verifies HTTPS, byte
size, and SHA-256 before an atomic install. Captured traffic can be checked with
the descriptor-driven round-trip validator; the published Tier C denominator
remains zero until a genuine capture is available.

## Honest floor and pbtk comparison

On the pinned, unobfuscated Mullvad APK, pbtk 1.1.2 currently recovers a more
faithful schema than ProtoLoom. This is the floor users should plan around, not
a hidden caveat:

| Metric | ProtoLoom | pbtk 1.1.2 |
|---|---:|---:|
| Field recall | 90.91% | 100.00% |
| Wire-type accuracy | 100.00% | 100.00% |
| Type fidelity | 50.37% | 100.00% |
| Name recovery | 90.74% | 100.00% |
| Structural fidelity | 16.36% | 100.00% |
| Enum recovery | 0.00% | 100.00% |
| Compile rate | 100.00% | 100.00% |

ProtoLoom's advantage is a bounded direct parser with explicit confidence and
no decompiler in its normal path, not better fidelity on every target. The full
comparison, methodology, precision and label measurements are in
`benchmarks/results.md`; `scripts/compare_pbtk.sh` reproduces the differential.

## Architecture

`model.py` is the dependency-free spine. Container readers expose binary
structure, extractors return evidence, decoders build the model, reconciliation
chooses field-level winners while retaining conflicts, and emitters know nothing
about DEX or native formats. Import-linter enforces the boundary in CI.
The roadmap's proposed `bench/build.py` is intentionally folded into
`bench/corpus.py`, where compilation jobs and corpus manifests share one real
implementation instead of a compatibility re-export.

## Development

Create the reproducible environment with micromamba:

```console
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv make check
```

For a faster Python-only setup, run `uv sync --extra dev --group dev` followed by
`uv run make check`; it uses the same dependency declaration.

Release tags build wheel/sdist packages, PyInstaller binaries for Linux x86-64,
Linux arm64, and macOS arm64, plus a multi-architecture GHCR image. Publishing
requires the repository owner's PyPI trusted-publisher and GitHub environment
configuration.

## License

Apache License 2.0.
