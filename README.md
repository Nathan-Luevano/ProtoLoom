<div align="center">

# ProtoLoom

### Recover protobuf schemas from stripped binaries

**Deterministic extraction for Android, native, and Go artifacts — with
field-level evidence, explicit uncertainty, and reproducible benchmarks.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-3DA639)](LICENSE)
[![Typing: strict](https://img.shields.io/badge/typing-strict-2A6DB0)](pyproject.toml)
[![Tests: 151](https://img.shields.io/badge/tests-151%20passing-20A162)](tests)

[Quick start](#quick-start) · [How it works](#how-it-works) ·
[Benchmarks](#measured-results) · [Limitations](#current-limitations) ·
[Development](#development)

</div>

---

ProtoLoom turns protobuf evidence left inside compiled software into useful,
compilable schema artifacts. Its normal recovery path is bounded, deterministic,
and independent of both LLMs and decompilers.

```text
app.apk / classes.dex / native binary
                  │
          inspect and extract
                  │
       validate and reconcile evidence
                  │
                  ▼
  .proto  ·  descriptor set  ·  JSON evidence  ·  reports
```

## Why ProtoLoom?

- **Useful output, not a text dump.** Recovery produces compilable `.proto`
  files, a binary descriptor set, structured evidence, a Markdown report, and
  a self-contained HTML dashboard.
- **Confidence is part of the result.** Every field retains its source,
  confidence, and conflicts. Unsupported Lite bytecode becomes a visible
  bail-out instead of a silent guess.
- **Decompiler-free by default.** Direct parsers handle the normal path. jadx
  is available only as an explicit, bounded fallback for Android context.
- **Built against ground truth.** Hash-pinned fixtures, upstream schemas,
  multiple protobuf-javalite versions, R8 variants, and shipping APKs exercise
  the same public CLI users run.
- **Safe with hostile inputs.** Container reads, downloads, subprocesses, and
  archive extraction are bounded and validated.

## Supported evidence

| Input | Detection and traversal | Recovery path |
|---|:---:|---|
| APK, AAB, JAR, ZIP | ✓ | DEX Lite metadata and embedded descriptors |
| Raw DEX | ✓ | Bounded `newMessageInfo` dataflow |
| ELF | ✓ | Sections, segments, native and Go descriptors |
| Mach-O, including fat binaries | ✓ | Section-based descriptor scanning |
| PE and generic binaries | ✓ | Embedded descriptor scanning |
| Gzip-wrapped Go metadata | ✓ | `FileDescriptorProto` recovery |

ProtoLoom currently understands two primary evidence families:

1. Embedded `FileDescriptorProto` data, including gzip-wrapped Go descriptors.
2. Protobuf Lite `newMessageInfo` metadata recovered through bounded DEX
   register tracking.

## Quick start

ProtoLoom requires Python 3.11 or newer.

```console
# From a local checkout
uv tool install .
```

Inspect an artifact before extracting it:

```console
protoloom inspect app.apk
protoloom extract app.apk --output out
```

Then open `out/report.md` or `out/dashboard/index.html`, and use the recovered
`.proto` files in the protobuf toolchain of your choice.

Useful commands:

```console
# Verify optional tools and the local environment
protoloom doctor

# Run a self-contained end-to-end recovery
protoloom demo

# Keep bounded jadx context for difficult Android artifacts
protoloom extract app.apk --jadx --jadx-timeout 120 --output out

# Explicitly permit uncertain Lite array-order recovery
protoloom extract app.apk --allow-heuristic-lite --output out

# Reproduce the local benchmark calibration
protoloom bench --corpus tier-a-small --per-target
```

Heuristic Lite recovery is off by default. When enabled, every heuristic result
remains counted as uncertainty in the report.

## Recovery output

A successful extraction creates a portable investigation bundle:

```text
out/
├── *.proto                 recovered schema source
├── <input-name>.desc       binary descriptor set
├── recovery.json           structured evidence and confidence
├── report.md               human-readable findings
├── dashboard/index.html    self-contained recovery dashboard
└── jadx/                   optional decompiled context and candidate index
```

The jadx directory is created only when `--jadx` is requested. The fallback
runs without a shell, has a configurable timeout, preserves bounded diagnostics,
and fails visibly when Java or jadx is unavailable.

## How it works

```text
Container readers ──► evidence extractors ──► schema model
                                                │
                           conflict reconciliation
                                                │
                                                ▼
                              proto · JSON · reports · dashboard
```

The dependency-free model is the project spine. Container readers expose
binary structure; extractors produce evidence; decoders build schemas;
reconciliation retains field-level conflicts; emitters depend only on the
model. Seven import-linter contracts enforce these boundaries in CI.

The roadmap proposed a separate `bench/build.py`. Its responsibilities live in
`bench/corpus.py` so compilation jobs and corpus manifests use one substantive
implementation rather than a compatibility façade.

## Measured results

ProtoLoom publishes denominators, negative results, uncertainty, and known
information ceilings. The complete methodology and reproducible tables live in
[`benchmarks/results.md`](benchmarks/results.md).

### Exact descriptor path

The pinned upstream suite spans protobuf, Google APIs, gRPC, and Envoy:

| Measurement | Result |
|---|---:|
| Root descriptors | 13 / 13 exact |
| Descriptor bytes | 32,397 / 32,397 identical |
| Recovered fields | 549 / 549 |
| Recovered enum values | 64 / 64 |
| Recompiled schemas | 13 / 13 |
| Optimized C++ objects | 0 / 3 |
| Captured-payload round trips | Not measured — 0 payloads |

The perfect rows measure binaries containing complete embedded descriptors.
They do **not** claim that arbitrary generated native code can be reconstructed:
the retained 0/3 C++ result demonstrates that boundary.

### Protobuf Lite matrix

The pinned matrix covers protobuf-javalite 3.21.12, 4.29.3, and 4.35.1 plus
default and aggressive R8. Across all five legs it recovers all 13 fields with
100% precision and wire accuracy, and every emitted schema compiles and passes
the fixture round trip. Exact type fidelity ranges from 61.54% to 69.23%; enum
and map recovery vary when R8 removes the evidence needed to prove them.

### Real applications

Tier B pins eight release APKs across seven independent schema families. Runs
include Signal, Molly, Mullvad, Bitwarden Authenticator, Meshtastic, Flipper,
Gadgetbridge, and Smartspacer. These results include hard negatives and partial
recoveries; they are not collapsed into a flattering aggregate.

On the pinned Mullvad comparison, ProtoLoom reaches 90.91% field recall and
100% wire accuracy, but only 50.37% exact type fidelity and 16.36% structural
fidelity. Those gaps are implementation losses, not presented as unavoidable
ambiguity.

## Current limitations

ProtoLoom is alpha software. Before adopting it, plan around these boundaries:

- R8 can remove names, getter associations, map initializers, and nesting
  evidence. ProtoLoom will not invent information that is no longer provable.
- Lite enum, map, nested-type, and exact scalar recovery remain
  optimizer-sensitive.
- The direct scanner does not recover descriptors from ordinary modern
  protoc-generated C++ objects unless a usable descriptor carrier remains.
- Tier C captured-traffic validation is still unmeasured because the pinned
  projects do not publish qualifying redistributable payload captures.
- jadx preserves and indexes decompiled context; it does not convert arbitrary
  decompiler output into trusted schema evidence.

### Honest floor: pbtk

On the pinned unobfuscated Mullvad APK, pbtk 1.1.2 currently recovers a more
faithful schema. ProtoLoom's advantage is bounded direct parsing, visible
confidence, deterministic evidence, and no decompiler requirement — not higher
fidelity on every artifact.

| Metric | ProtoLoom | pbtk 1.1.2 |
|---|---:|---:|
| Field recall | 90.91% | 100.00% |
| Wire-type accuracy | 100.00% | 100.00% |
| Type fidelity | 50.37% | 100.00% |
| Name recovery | 90.74% | 100.00% |
| Structural fidelity | 16.36% | 100.00% |
| Enum recovery | 0.00% | 100.00% |
| Compile rate | 100.00% | 100.00% |

Run [`scripts/compare_pbtk.sh`](scripts/compare_pbtk.sh) to reproduce the
differential.

## Reproducing validation

Run every local quality gate:

```console
uv sync --extra dev --group dev
uv run make check
```

The gate includes Ruff linting and formatting, strict mypy, property tests,
the no-docstrings policy, architectural contracts, and the complete test suite.

Corpus entry points:

```console
# Pinned upstream descriptor corpus
uv run python scripts/run_tier_a_upstream.py \
  --protoc /path/to/protoc \
  --cxx /path/to/c++ \
  --protobuf-include /path/to/protobuf/include
uv run protoloom bench --corpus tier-a-upstream --per-target

# Hash-verify and fetch selected real APKs without vendoring them
uv run python scripts/fetch_real_apps.py --help
```

Real APKs are never redistributed by this repository. Downloads require HTTPS
and are verified by exact byte size and SHA-256 before atomic installation.

## Development

For the complete native toolchain, create the pinned micromamba environment:

```console
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv make check
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution expectations and
[`SECURITY.md`](SECURITY.md) for responsible disclosure. Release tags build
wheel and source distributions, standalone Linux and macOS binaries, and a
multi-architecture container image. Publishing remains gated by repository
owner configuration.

## Documentation

- [Benchmark methodology and full results](benchmarks/results.md)
- [Corpus provenance and reproduction](benchmarks/corpus/README.md)
- [Protobuf Lite metadata format](docs/protobuf-lite-format.md)
- [Engineering notes and research journal](docs/blog)
- [Implementation roadmap](PROTOLOOM-ROADMAP.md)

## License

ProtoLoom is available under the [Apache License 2.0](LICENSE).
