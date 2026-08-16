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
decoder is pinned to protobuf v33.6 upstream source and has format/dataflow unit
coverage, but the roadmap's javalite accuracy thresholds have not yet been
measured on the full Android compilation matrix. The published benchmark table
is therefore a harness calibration, not an extractor accuracy claim.

## Quick start

```console
uv tool install .
protoloom inspect app.apk
protoloom extract app.apk --output out
protoloom demo
protoloom bench --corpus tier-a-small --per-target
protoloom doctor
```

The `extract` command scans APK/AAB DEX and native members as well as raw DEX,
ELF, Mach-O, PE, Go, and generic binary inputs. Unsupported lite bytecode is a
visible bail-out, never a silently guessed schema.

## Validation

Run every local gate with:

```console
uv run make check
```

The suite enforces Ruff formatting and linting, strict mypy, zero docstrings,
layer boundaries, and the test suite. `protoloom demo` performs a complete
recovery and emits operational artifacts without downloading a corpus.

Real-app APKs are not redistributed. The Tier B manifest pins official Signal,
Molly, and Mullvad release artifacts and matching immutable source revisions;
`scripts/fetch_real_apps.py` verifies HTTPS, byte size, and SHA-256 before an
atomic install. Captured traffic can be checked with the descriptor-driven
round-trip validator.

## Architecture

`model.py` is the dependency-free spine. Container readers expose binary
structure, extractors return evidence, decoders build the model, reconciliation
chooses field-level winners while retaining conflicts, and emitters know nothing
about DEX or native formats. Import-linter enforces the boundary in CI.

## Development

Create the reproducible environment with micromamba:

```console
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv make check
```

For a faster Python-only setup, run `uv sync --extra dev` followed by
`uv run make check`; it uses the same dependency declaration.

Release tags build wheel/sdist packages, PyInstaller binaries for Linux x86-64,
Linux arm64, and macOS arm64, plus a multi-architecture GHCR image. Publishing
requires the repository owner's PyPI trusted-publisher and GitHub environment
configuration.

## License

Apache License 2.0.
