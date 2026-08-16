# PROTOLOOM — Master Build Roadmap

Recovering `.proto` schemas from stripped binaries, with published accuracy numbers.

This document is the single source of truth for the project. It is written to be
handed to an implementing agent (or future-you at 2am) and followed literally.
Every milestone has an **exit criterion** — a command that either passes or
fails. If a command doesn't pass, the milestone isn't done. No judgement calls.

---

## 0. Ground rules (read before writing any code)

These are non-negotiable and apply to every commit in the project.

### 0.1 Authorship

Every commit is authored by Nathan Luevano and nobody else. No exceptions.

- No `Co-Authored-By:` trailers, ever.
- No `Generated with ...` / `Co-authored-by: Claude` / tool attribution footers.
- No `--author` overrides.
- Agents commit **as you**, using your configured identity, or they don't commit.

Set this once at repo init:

```bash
git config user.name "Nathan Luevano"
git config user.email "<your github noreply or real email>"
```

Enforce it mechanically — a repo-local hook that rejects any commit carrying an
attribution trailer. Create `.githooks/commit-msg`:

```bash
#!/bin/sh
if grep -qiE '^(co-authored-by|generated (with|by)|signed-off-by: (?!Nathan))' "$1"; then
  echo "blocked: commit message carries a foreign attribution trailer"
  exit 1
fi
```

```bash
chmod +x .githooks/commit-msg
git config core.hooksPath .githooks
```

Also add a CI job (Milestone 0) that fails the build if `git log` on the PR range
contains `Co-Authored-By` or a non-matching author email. Hooks can be bypassed
with `--no-verify`; CI cannot.

### 0.2 Commit granularity

One logical change per commit. Every file creation, modification, and deletion
lands in a commit — nothing is batched into a "wip" dump, nothing is amended
into oblivion after the fact.

Convention (matches AutoFTE):

```
<type>(<scope>): <imperative summary under 72 chars>

<body: what changed and, more importantly, why. wrap at 80.>
```

Types: `feat`, `fix`, `refactor`, `test`, `bench`, `docs`, `chore`, `perf`.
Scopes: `dex`, `lite`, `descriptor`, `native`, `emit`, `bench`, `cli`, `corpus`.

Rules:
- A deletion is its own commit unless it's an atomic part of a rename/move.
- A commit that changes measured accuracy **must** state the before/after
  numbers in the body and update `benchmarks/results.md` in the same commit.
- Never squash the benchmark history. The history *is* the deliverable.

### 0.3 No docstrings

Zero docstrings anywhere in the codebase. Not module-level, not class-level, not
function-level. Comments are allowed, should be informal, and should be rare —
they explain *why*, never *what*.

Good:
```python
# protobuf 3.9 moved field names into objects[0] as one space-joined string.
# older versions put them in separately. we sniff which by checking type.
```

Bad:
```python
def parse_info_string(s: str) -> MessageInfo:
    """Parse a protobuf-lite info string into a MessageInfo."""
```

Enforce with a CI check, since no linter bans docstrings natively.
`scripts/no_docstrings.py`:

```python
import ast, pathlib, sys

bad = []
for p in pathlib.Path("src").rglob("*.py"):
    tree = ast.parse(p.read_text())
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(
        n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for n in nodes:
        if ast.get_docstring(n):
            bad.append(f"{p}:{getattr(n, 'lineno', 1)}")

if bad:
    print("docstrings found:\n" + "\n".join(bad))
    sys.exit(1)
```

Wire into CI and into `make check`. Also set ruff to not *require* them:
`ignore = ["D"]` in `pyproject.toml`.

### 0.4 Anti-vibe-coding contract

The failure mode for this project is an agent producing a plausible-looking
info-string parser that is subtly wrong and passes its own hand-written tests.
Three defenses, all mandatory:

1. **Never infer a format from a decompiler screenshot.** The protobuf-lite
   encoding is defined in upstream Java source. Read it. The authoritative
   files, in `protocolbuffers/protobuf`:
   - `java/core/src/main/java/com/google/protobuf/RawMessageInfo.java`
   - `java/core/src/main/java/com/google/protobuf/MessageSchema.java`
     (specifically `newSchemaForRawMessageInfo`)
   - `java/core/src/main/java/com/google/protobuf/GeneratedMessageLite.java`
     (`dynamicMethod`, `BUILD_MESSAGE_INFO`)
   - `java/core/src/main/java/com/google/protobuf/MessageInfo.java`
   Pin the exact upstream commit SHA you read in a comment. When behavior
   differs across protobuf versions, that pin is how you debug it.
2. **Differential oracle, always.** For every synthetic corpus entry you have the
   original `.proto`. The tool's output is checked against it automatically, not
   by eye. If you can't check it automatically, it doesn't count as working.
3. **Round-trip or it didn't happen.** A recovered schema that can't decode and
   byte-identically re-encode real captured payloads is a hypothesis, not a
   result.

### 0.5 Definition of done, per milestone

A milestone is done when: exit-criterion command passes, `make check` passes,
`benchmarks/results.md` reflects any accuracy change, the blog post draft for
that milestone exists in `docs/blog/`, and everything is committed.

---

## 1. Name and identity

**Recommended: `PROTOLOOM`** (package: `protoloom`, CLI: `protoloom`).

Reasoning: it sits in your existing naming family (TraceLoom), "loom" carries the
weaving-a-structure-back-together idea which is literally what the tool does, and
it's short enough to type. It reads as a tool, not a paper.

Alternates if taken, in preference order: `protoglean`, `PROTOSPECTER`,
`unweave`, `protosalvage`.

**Do not use `protoscope`** — Google ships a tool by that name for wire-format
inspection, and colliding with the vendor of the format you're reversing is a
bad first impression.

Verify availability before committing to it:

```bash
pip index versions protoloom          # must 404
curl -sI https://pypi.org/project/protoloom/   # must be 404
# and check github.com/search?q=protoloom
```

Tagline for the repo description (keep it concrete, like AutoFTE's):

> Recovers compilable `.proto` schemas from stripped Android, native, and Go
> binaries. Measured against real ground truth, not vibes.

---

## 2. Distribution decision

**Ship all three, in this order. And actually tag the release this time.**

The single biggest lesson from AutoFTE is sitting in its own README: the headline
install doesn't resolve because the tag was never pushed. Everything is wired,
nothing shipped. Do not repeat that here.

| Channel | Why | When |
|---|---|---|
| **PyPI via `pipx install protoloom`** | Primary. Security people expect a one-liner. This is the difference between a tool people use and a repo people star and forget. | Milestone 4 (first working extractor), as `v0.1.0` |
| **GitHub Releases + PyInstaller single-file** | RE workstations and air-gapped analysis boxes often have no usable Python. Attach `protoloom-linux-x86_64`, `-aarch64`, `-macos-arm64`. | Same tag, via release workflow |
| **Docker image (ghcr.io)** | The DEX path needs a JVM for the jadx fallback; the container removes that whole class of support question. Publish to GHCR, not Docker Hub (no rate limits, same auth as the repo). | Milestone 5 |

Not a Ghidra/Binary Ninja plugin at v1. Plugins fragment effort across two
disassembler APIs and gate your users behind a specific tool. Ship the CLI and a
clean library API first; a Binary Ninja plugin is a great **v2** and a great
second blog post, because by then the core is measured and stable.

Release automation: reuse AutoFTE's `.github/workflows/release.yml` shape —
trigger on `v*` tags, build wheel + sdist, `pypa/gh-action-pypi-publish` with
trusted publishing (OIDC, no API token in secrets), build PyInstaller binaries in
a matrix, attach to the GitHub Release. Use `softprops/action-gh-release`.

Versioning: `0.x.y` until the benchmark numbers stop moving by >2 points between
releases, then `1.0.0`. Be honest in the badge — `status: alpha` until Milestone 7.

---

## 3. What the tool actually does

### 3.1 The five recovery paths

Protobuf schemas survive compilation in different ways depending on runtime.
Each is a separate extractor with a separate confidence class.

| # | Path | Target | What survives | Confidence |
|---|---|---|---|---|
| 1 | **Embedded `FileDescriptorProto`** | C++, Go, Python, C#, full-Java | The *entire* schema, serialized, verbatim in `.rodata` or a gzip blob | **Certain** — it's the real descriptor |
| 2 | **protobuf-lite `newMessageInfo`** | Modern Android (the big one) | Field numbers, wire types, oneofs, hasbits, nesting, and field *names* unless R8 rewrites them | **High** |
| 3 | **javanano / javamicro codegen** | Legacy Android | Field numbers + types from generated read/write loops | **Medium** |
| 4 | **Native codegen pattern matching** | Stripped C++/ObjC with descriptors stripped | Field numbers + wire types from `WriteTag`/parse-loop constants | **Speculative** |
| 5 | **Wire-shape inference** | Anything, from captured traffic only | Field numbers + wire types, no names, no semantics | **Speculative** |

v1 scope: **paths 1 and 2.** Path 1 because it's high-value and mechanically
easy (it's the highest-confidence output in the whole tool, and it covers Go and
C++ which nothing modern handles cleanly). Path 2 because it's where the
incumbent explicitly falls over, and it's where the users are.

Paths 3–5 are v1.1+. Path 5 overlaps blackboxprotobuf; treat that as an
integration, not a competitor to reimplement.

### 3.2 Output artifacts

Every run produces:

- `out/<name>.proto` — human-readable, **must compile with `protoc`**
- `out/<name>.desc` — binary `FileDescriptorSet`. This is the artifact that
  makes the tool operational rather than academic: it loads directly into
  `protoc --decode`, `grpcurl -protoset`, mitmproxy addons, and Burp.
- `out/recovery.json` — structured record: every message, every field, and a
  per-field `confidence` (`certain` / `high` / `medium` / `speculative`) plus
  `evidence` (the byte offset, class name, or info-string index it came from)
- `out/report.md` — human summary, what was found, what was guessed, what failed
- `out/dashboard/index.html` — optional, same static self-contained shape as
  AutoFTE's

Field-level confidence is the differentiating honesty feature. pbtk hands you a
`.proto` with no indication of which parts it's sure about. You hand back a
schema where every field says how it was derived. That's the thing an RE trusts.

---

## 4. Architecture

Layered, with hard boundaries. The rule: **nothing downstream of `model` knows
what a DEX file or an ELF is.** Same discipline as TraceLoom's `NormalizedEvent`
— that's what let you swap event sources without touching detectors, and it's
exactly what will let you add Go/iOS support later without rewriting the emitter.

```
protoloom/
  model.py          RecoveredSchema, Message, Field, EnumType, Confidence,
                    Evidence — plain dataclasses, zero I/O, zero deps.
                    THE spine. Everything converges here.

  container/        "what kind of file is this and what's inside it"
    detect.py       magic-sniff: APK / AAB / DEX / ELF / Mach-O / PE / JAR
    apk.py          zip walk -> classes*.dex, lib/*/*.so, assets/
    elf.py          section + segment access (LIEF, pyelftools fallback)
    macho.py        __TEXT.__const / __cstring access
    dex.py          minimal DEX reader: string_ids, type_ids, method_ids,
                    class_defs, code_items. NOT a decompiler.

  extract/          "find candidate schema evidence" -> raw findings
    descriptor.py   PATH 1: scan for serialized FileDescriptorProto
    lite.py         PATH 2: find newMessageInfo call sites, pull info string
                    + objects array
    gozip.py        PATH 1b: Go's gzip'd rawDesc blobs

  decode/           "turn evidence into structure" -> model objects
    infostring.py   the protobuf-lite info-string state machine
    fieldtype.py    lite field-type id -> proto type + label + wire type
    names.py        java field name -> proto snake_case, obfuscation detect
    descpb.py       FileDescriptorProto -> model (thin, it's already a schema)

  reconcile.py      merge findings from multiple paths, resolve conflicts,
                    assign final confidence, build the type graph, resolve
                    cross-message references

  emit/
    proto.py        model -> .proto source text
    descset.py      model -> FileDescriptorSet bytes
    jsonout.py      model -> recovery.json
    report.py       model -> report.md
    dashboard.py    model -> static html

  validate/
    compile.py      run protoc on emitted .proto, capture errors
    roundtrip.py    decode+re-encode a real payload, assert byte equality

  bench/
    corpus.py       corpus manifest handling, fetch, verify
    build.py        drive the compilation matrix
    metrics.py      the metric definitions (see section 6)
    runner.py       `protoloom bench` entry point

  cli.py            typer app
  doctor.py         what's installed, what's missing
```

**Dependency direction is strictly downward.** `extract` may import `container`
and `model`. `decode` imports `model` only. `emit` imports `model` only.
`reconcile` imports `model` only. Nothing imports `cli`. Add a CI check with
`import-linter` to enforce this contract — it's cheap and it prevents the slow
architectural rot that turns a clean tool into a ball of mud by month three.

### 4.1 The key design call: no decompiler in the hot path

pbtk shells out to `jadx`/`dex2jar`/`chromium`. That's why it's fragile and why
its install instructions are a page long.

**PROTOLOOM parses DEX directly.** You need exactly three things from a DEX:
the string pool, the method reference table, and the bytecode of methods that
call `newMessageInfo`. That is a few hundred lines of struct parsing against a
stable, well-documented format (`source.android.com/docs/core/runtime/dex-format`).
No JVM, no subprocess, no version skew, and it's ~100x faster.

Keep `jadx` as an **opt-in fallback** (`--jadx`) for pathological obfuscation
where you need real decompiled context. Optional, never required. `doctor`
reports whether it's available.

---

## 5. The technical core, in detail

This section exists so the implementing agent has no room to improvise.

### 5.1 Path 1 — embedded FileDescriptorProto

**Where it lives.** protoc's C++ backend emits, per `.proto` file, a
`descriptor_table_<mangled_name>` structure whose `serialized_descriptor` points
at the full `FileDescriptorProto` serialized into read-only data. Go's
`protoc-gen-go` emits `file_<name>_rawDesc` (raw bytes in modern versions,
gzip-compressed in pre-APIv2 ones). Python emits it as a bytes literal passed to
`AddSerializedFile`. C# emits base64.

**The extraction algorithm** — brute-force parse with strict validation, because
you cannot rely on symbols in a stripped binary:

1. Pull candidate byte ranges: ELF `.rodata`, `.data.rel.ro`; Mach-O
   `__TEXT.__const`, `__DATA.__const`; for Go also scan for gzip magic `1f 8b`.
2. For every offset in the range (stride 1 is fine, these sections are small),
   attempt `FileDescriptorProto.ParseFromString` on `data[off:off+n]` for a
   growing `n`.
3. **Accept only on strong validation**, or you will drown in false positives:
   - parse consumes the buffer with no trailing garbage
   - `name` field is present and ends in `.proto`
   - `name` is valid UTF-8 and printable
   - at least one of `message_type`, `enum_type`, or `service` is non-empty
   - `syntax` is absent, `"proto2"`, or `"proto3"`
4. Deduplicate by `name`, then resolve `dependency` edges between recovered
   files into one `FileDescriptorSet`.

Optimization that makes step 2 tractable: descriptors always begin with field 1
(`name`), wire type 2 — byte `0x0A` — followed by a varint length and then a
run of printable ASCII ending in `.proto`. **Seek `0x0A` bytes first**, validate
the name shape, and only then attempt the full parse. This turns an O(n) parse
storm into a handful of candidate offsets.

Confidence for everything from this path: `certain`. It *is* the schema. Field
names, comments-in-name form, `json_name`, options — all intact.

**This path alone beats every existing tool on Go binaries**, which nothing in
the current landscape handles well. Cheap win, ship it first.

### 5.2 Path 2 — protobuf-lite info strings (the hard, valuable one)

**How it works.** `GeneratedMessageLite` subclasses implement `dynamicMethod`.
For `BUILD_MESSAGE_INFO` they call:

```java
newMessageInfo(DEFAULT_INSTANCE, infoString, objectsArray)
```

`infoString` is a `String` whose *characters* encode a packed integer stream
describing the message layout. `objectsArray` is an `Object[]` holding field
names, nested message `Class` refs, and enum verifiers.

**Do not guess this format.** Read `RawMessageInfo.java` and
`MessageSchema.newSchemaForRawMessageInfo` at a pinned upstream commit and
implement exactly what they do. The broad shape, so you know what you're looking
at when you get there:

- Characters encode integers with a continuation scheme (values below a
  threshold are literal; at/above it, the next char carries more bits).
- The stream opens with a header block: flags (proto2/proto3, message-set),
  field count, oneof count, hasbits count, min/max field number, entry count,
  map field count, repeated field count.
- Then one entry per field: field number, a type-and-flags byte, and
  conditionally a hasbits index / oneof index / auxiliary-object index.
- `objects[0]` in modern protobuf is a single `String`: the message's own name
  followed by space-separated Java field names (`"Foo bar_ baz_ qux_"`).
  Older versions passed names as separate array elements. **Sniff which layout
  you're looking at by type-checking element 0** — don't assume a version.

**Getting the operands out of DEX.** The info string and the objects array are
built in bytecode, so you must do lightweight dataflow, not just string grepping:

1. Find `newMessageInfo` in `method_ids`; find all call sites.
2. For each call site, walk backwards in the `code_item` to resolve the register
   holding the string: `const-string` / `const-string/jumbo` gives you a
   `string_ids` index directly.
3. The `Object[]` is built by `new-array` + a run of `const/4` index,
   `sput`/`aput-object` from `const-string` or `const-class`. Walk that
   construction sequence and record each element in order.
4. Field names may instead live in a static field initialized in `<clinit>` —
   handle that as a fallback.

This is a small, bounded abstract interpreter over a handful of DEX opcodes. Do
not build a general one. Whitelist the opcodes you handle and **loudly emit a
`speculative` finding with a reason when you hit an opcode outside the
whitelist**, rather than silently producing a wrong answer. Track the count of
these bail-outs and print it on every run — this is your `No-hash fallbacks`
counter from AutoFTE, and it's what keeps the tool auditable.

**Name recovery and R8.** Field names come back as Java identifiers with a
trailing underscore (`user_id_` → `user_id`). Under R8/ProGuard with obfuscation
on, they may be rewritten to `a_`, `b_`. Detect this: if >50% of a message's
field names match `^[a-z]{1,2}_?$`, mark names `speculative`, emit
`field_1`-style placeholders alongside, and say so in the report. **Measuring
exactly where this cliff is, is one of your headline benchmark results.**

### 5.3 Reconciliation

When multiple paths produce a schema for the same message, merge by:
`certain` > `high` > `medium` > `speculative`, field by field, not
message by message. Record every conflict in `recovery.json` with both
candidates. Never silently drop the loser — a conflict is information.

### 5.4 Emission

`.proto` emission has one hard requirement: **the output must compile.** Which
means:
- unknown/unresolvable message types become synthesized placeholder messages,
  not dangling references
- reserved keywords in recovered names get suffixed
- `proto3` optional/oneof semantics get emitted correctly (a synthetic oneof for
  proto3 `optional` is easy to get wrong — test it explicitly)
- imports for well-known types (`google/protobuf/timestamp.proto` etc.) emitted
  when referenced

`validate/compile.py` runs `protoc` on every emitted file as part of the test
suite and the benchmark. A schema that doesn't compile scores zero. No partial
credit, no exceptions.

---

## 6. Metrics — define these before writing the extractor

Write `bench/metrics.py` and its tests **first**, in Milestone 3, before the
extractors exist. This is the discipline that produced AutoFTE's credibility:
you built the measuring stick before you had a reason to want a flattering
number from it.

Matching is by field number within a matched message; messages are matched by
fully-qualified name where available, else by structural signature.

| Metric | Definition | Why it matters |
|---|---|---|
| **Field recall** | recovered fields / ground-truth fields | Did you find everything |
| **Field precision** | correct fields / recovered fields | Are you hallucinating fields |
| **Wire-type accuracy** | correct wire type / matched fields | Can the schema actually decode traffic |
| **Type fidelity** | exact proto type match (int32 vs sint32 vs uint32) / matched fields | Strictly harder than wire type; some info is genuinely unrecoverable — report the ceiling |
| **Name recovery rate** | exact-name matches / matched fields | The R8 cliff metric |
| **Label accuracy** | optional/repeated/required correct | |
| **Structural fidelity** | correct nesting + oneof grouping / ground-truth groups | |
| **Enum recovery** | recovered enum values / ground truth | |
| **Compile rate** | emitted files that `protoc` accepts / total | Binary gate; the operational floor |
| **Round-trip rate** | payloads that decode + re-encode byte-identically / total | The metric that can't be gamed |

Report **both macro (per-target mean) and micro (pooled)**, exactly as AutoFTE
does, and lead with whichever is *less* flattering. Report per-target spread with
`--per-target`. Publish the worst target by name and root-cause it in a
`scripts/diagnose_*.py` script — that single habit is why your GitHub reads as
an engineer's rather than a student's.

Also compute and publish **ceilings** where they exist. Example: `int32` and
`sint32` are indistinguishable in a lite info string when no varint-zigzag hint
survives; that caps type fidelity below 100%, and saying so pre-empts the
"why isn't this 100%" question with a real answer.

---

## 7. Validation — the answer to "not just synthetic data"

Four tiers, increasing in realism. You need all four; each covers the others'
blind spot.

### Tier A — Synthetic from real schemas (perfect ground truth, huge N)

Not "synthetic" in the weak sense. The *schemas* are real production Google API
definitions; only the compilation is yours. Ground truth is exact.

**Corpus sources** (all permissively licensed, all fetchable and hash-pinnable):

| Source | What | Approx N | License |
|---|---|---|---|
| `googleapis/googleapis` | Google's entire public API surface | 3,000+ `.proto` | Apache-2.0 |
| `protocolbuffers/protobuf` conformance + unittest protos | Deliberately hostile edge cases — every type, deep nesting, weird oneofs, extensions | ~50, high value | BSD-3 |
| `grpc/grpc-proto` | gRPC service definitions | ~40 | Apache-2.0 |
| `envoyproxy/data-plane-api` | Large, deeply-nested real-world config schemas | 300+ | Apache-2.0 |
| `etcd-io/etcd`, `kubernetes/api` | Go-ecosystem, exercises the Go path | 100+ | Apache-2.0 |

**Compilation matrix.** This is where the interesting findings live:

- runtimes: `java`, `javalite`, `kotlin`, `cpp`, `go`, `csharp`
- Android packaging: raw DEX, and APK built through D8 and R8
- R8 config: off / `-dontobfuscate` / default / aggressive (`-repackageclasses`,
  `-allowaccessmodification`)
- native opt: `-O0`, `-O2`, `-Os`; stripped and unstripped; ARM64 and x86-64
- protobuf runtime versions: pin at least three (e.g. 3.21.x, 4.x, latest) —
  the info-string format has changed across versions and **demonstrating that
  you handle all three is itself a selling point**

Drive it with a `Makefile` + a small Gradle project for the Android legs.
Hash-pin every corpus fetch like AutoFTE's `fetch_bench_corpus.sh` does.

### Tier B — Real shipping apps with public schemas (real ground truth, small N)

**This is the tier that answers your question, and it's the one nobody has done.**

There exist real, shipping, obfuscated Android apps that use protobuf **and**
publish their `.proto` files in a public repo. Run PROTOLOOM on the official
release APK; compare against the `.proto` in the upstream source tree. Real
build pipeline, real R8 config, real ground truth.

Candidates to evaluate (verify each actually uses protobuf and ships `.proto`
before committing to it):

- **Signal-Android** — protobuf-heavy, `.proto` files public, reproducible
  builds published
- **Molly** (Signal fork), **Briar**, **Element Android**
- **Mullvad VPN app** — Rust core + Android, protos public
- **Tailscale Android**, **Bitwarden Android**
- **WireGuard**, **Wire Android**
- Any AOSP component shipping protobuf (`.proto` in the AOSP tree)

Target 8–12 apps. That's plenty — this tier is about realism, not N, and Tier A
supplies statistical power.

**Legal/ethical hygiene:** only apps whose APK you can obtain legitimately
(GitHub Releases, F-Droid, official site). Don't redistribute APKs in the repo —
ship a manifest of URLs + SHA256 and a fetch script, same pattern as AutoFTE's
corpus. Nothing proprietary vendored. Note it in `SECURITY.md`.

### Tier C — Round-trip on captured traffic (no ground truth needed, self-validating)

Take a recovered schema, take a real captured protobuf payload, decode with the
schema, re-encode, assert **byte-identical**. If it round-trips, the schema is
correct for every field exercised by that payload — regardless of whether you
know the original `.proto`.

This is powerful precisely because it needs no ground truth, so it works on
targets where you'll never have any. Generate payloads for Tier A from the known
schemas; for Tier B, capture from the app in an emulator via mitmproxy.

Report round-trip rate as a headline metric.

### Tier D — Differential against pbtk

Run pbtk on the same corpus. Publish a comparison table. Be scrupulously fair:
pbtk is a 2017 tool doing an honest job and its author documented its own limits
openly — that's rare and worth respecting. Frame it as "here is where each tool's
coverage lies," not as a takedown. Do this and the pbtk author becomes a
collaborator and an amplifier, not a rival.

Ship a `scripts/compare_pbtk.sh` so the comparison is reproducible by anyone.

---

## 8. Tech stack

Deliberately boring where boring is fine, so all the novelty budget goes into the
extractors.

| Layer | Choice | Rationale | Fallback if it fails |
|---|---|---|---|
| Language | Python 3.11+ | Matches your existing repos; `protobuf` lib is first-party; RE community lives here | Rust for the DEX scanner only, via PyO3, if perf becomes real |
| Env/deps | **micromamba** (primary) + `uv` inside it (optional, for fast pip resolution) | You need conda-forge for `protoc`, a JDK, and binutils anyway — pip can't give you those. Same pattern as TraceLoom/SPECTER-FUSE. See §8.5 | pure `uv` + system-installed protoc/JDK, if you're willing to manage those by hand |
| CLI | `typer` | Same as SPECTER-FUSE; subcommands + `--help` for free | `argparse` |
| Protobuf | `protobuf` (`google.protobuf.descriptor_pb2`) | You need real `FileDescriptorProto` parsing — do not hand-roll it | none; this is required |
| ELF/Mach-O/PE | `LIEF` | One API for all three, actively maintained | `pyelftools` + `macholib` |
| DEX | **hand-rolled minimal reader** | See 4.1. Bounded, fast, no JVM | `androguard` (slow, heavy) or `jadx` CLI |
| Android build (bench) | Gradle + D8/R8 from AGP | Needed for a *real* R8 corpus | `r8.jar` standalone from maven |
| `protoc` | pinned release binaries, several versions | Compile-validation + corpus building | `grpcio-tools` bundled protoc |
| Testing | `pytest`, `hypothesis` | Property tests on the info-string decoder are extremely high-value: generate random valid schemas, compile, recover, assert round-trip | pytest alone |
| Lint/type | `ruff`, `mypy --strict` | Same as TraceLoom | |
| Arch enforcement | `import-linter` | Keeps 4.0's layering honest | manual review (worse) |
| CI | GitHub Actions | matrix over python + protobuf versions | |
| Docs | plain markdown + the README as the artifact | Your READMEs are already your best writing. Keep the good stuff there. | |
| Dashboard | self-contained HTML, no framework | Same as AutoFTE — drop into CI artifacts, open offline | |

**No LLM in the core path.** Deliberate, and worth stating in the README. This
tool is deterministic and verifiable; that's the whole point and it differentiates
you from the wave of LLM-wraps-a-diff projects. An optional local-Ollama pass
that *guesses semantic names for obfuscated fields* is a legitimate v2 feature —
clearly fenced, always `speculative`, never in the default path.

### 8.5 Environment: WSL2 Ubuntu + micromamba

The whole project targets **WSL2 Ubuntu with micromamba**. This is not incidental
— several milestones (Android emulator, USB device access, Docker) have WSL-
specific failure modes that will eat a weekend if you meet them unprepared.

#### 8.5.1 Repo location — the one that bites everyone

**Clone into the Linux filesystem (`~/code/protoloom`), never `/mnt/c/...`.**

The 9p filesystem bridge to the Windows drive is roughly an order of magnitude
slower on the many-small-files access pattern that git, pytest collection, and
your DEX corpus all hammer. It also mangles permissions (everything shows `777`)
and invites CRLF corruption into binary fixtures. On `/mnt/c`, a full corpus
benchmark run that should take 3 minutes can take 30.

If you need to open the repo from Windows tooling, go the other direction: use
the `\\wsl$\Ubuntu\home\<you>\code\protoloom` UNC path, or VS Code's Remote-WSL
extension, which runs the server inside WSL and is the right answer.

#### 8.5.2 Line endings and binary fixtures

Your corpus contains DEX, ELF, APK, and `.desc` files. A stray CRLF conversion
silently corrupts every one of them and produces test failures that look like
parser bugs. Lock it down in M0:

```bash
git config core.autocrlf false
git config core.eol lf
```

`.gitattributes` (commit this in M0, before any fixture lands):

```
* text=auto eol=lf

*.dex     binary
*.apk     binary
*.aab     binary
*.so      binary
*.desc    binary
*.pb      binary
*.bin     binary
*.jar     binary
*.class   binary
tests/fixtures/** binary
```

#### 8.5.3 micromamba layout

Follow the SPECTER-FUSE pattern — `environment.yml` manages the interpreter and
the system-level tools; `pyproject.toml` stays the single source of truth for
Python dependencies.

`environment.yml`:

```yaml
name: protoloom
channels:
  - conda-forge
dependencies:
  - python=3.11
  - protobuf                # runtime lib
  - libprotobuf             # brings protoc
  - openjdk=17              # D8/R8 + Gradle for the Android corpus legs
  - binutils                # readelf/objdump for cross-checking extraction
  - unzip
  - make
  - pip
  - pip:
      - -e .[dev]
```

Create it in-project so it's disposable and reproducible:

```bash
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv pytest -q
```

Use `micromamba run -p ./.venv <cmd>` everywhere rather than relying on an
activated shell — it's what makes the `Makefile` work identically in CI and
locally, and it removes a whole class of "worked in my terminal" bugs.

Add to `~/.bashrc` if you haven't:

```bash
export MAMBA_ROOT_PREFIX="$HOME/.micromamba"
eval "$(micromamba shell hook -s bash)"
```

**Pinning caveat:** conda-forge's `libprotobuf` gives you *one* protoc version,
but §7 requires building the corpus against **three** protobuf versions. Don't
fight conda for this. Fetch pinned `protoc-*-linux-x86_64.zip` release archives
from the protobuf GitHub releases into `.protoc/<version>/`, hash-verify them
like AutoFTE's corpus script does, and have the benchmark driver select by
version. conda's protoc is just your convenience default.

#### 8.5.4 `.wslconfig` — give it enough memory

The descriptor brute-force scan holds large sections in memory, and Gradle/R8 is
a JVM that will happily eat everything you give it. Default WSL2 memory (50% of
host RAM) is usually fine, but if you see OOM kills during the Android corpus
build, create `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=12GB
swap=8GB
processors=8
networkingMode=mirrored
```

Then `wsl --shutdown` from PowerShell and restart. `networkingMode=mirrored`
requires a recent WSL (`wsl --version`) and is strongly recommended — see below.

#### 8.5.5 Networking: the Tier C capture problem

This is the WSL-specific issue that will actually cost you time, and it lands in
**M5**. Tier C round-trip validation needs real captured protobuf payloads from a
running Android app, which means an emulator plus mitmproxy.

**The Android emulator will not run inside WSL2 in the general case.** It needs
KVM, and WSL2 is itself a VM — nested virtualization is off by default and is
fiddly to enable. Don't fight this.

Recommended arrangement, in preference order:

1. **Emulator on the Windows host, mitmproxy in WSL.** Run Android Studio's
   emulator natively on Windows; point its proxy at mitmproxy listening in WSL.
   With `networkingMode=mirrored` set, WSL and Windows share a network namespace
   and this Just Works via `localhost`. Without mirrored mode, you need the WSL
   VM's IP (`ip addr show eth0`) and a `netsh portproxy` rule on the Windows
   side, which changes on every reboot and is miserable.
2. **Everything on Windows for capture only.** Run mitmproxy on Windows too,
   dump flows to a file, and copy the flow dump into WSL for analysis. The
   payload bytes are all PROTOLOOM needs — it never has to see the live traffic.
   This is the boring, robust option and I'd default to it.
3. **Physical device over USB.** Requires `usbipd-win` on the Windows side to
   attach the USB device into WSL (`usbipd bind` / `usbipd attach --wsl`).
   Workable, but an extra moving part. Simpler alternative: run the `adb` server
   on Windows and talk to it from WSL with `adb -H <windows-host-ip> -P 5037`.

**Scope guard:** Tier C does not require you to be the one who captures the
traffic. For Tier A you generate payloads directly from the known schemas — pure
Python, no emulator, no networking, and that alone gives you a publishable
round-trip rate. Treat live app capture as the *bonus* leg of Tier C. If the
networking setup fights you for more than a day, ship the Tier A round-trip
numbers and move on; note the limitation in the README the way TraceLoom notes
its sparse-label caveat.

#### 8.5.6 Docker under WSL

M5 ships a container image. Two options:

- **Docker Desktop with WSL2 integration** — enable your Ubuntu distro in
  Settings → Resources → WSL Integration. `docker` then works from your WSL
  shell. Easiest, and fine for personal use.
- **Native Docker inside WSL** — install `docker.io` in the distro and enable
  systemd via `/etc/wsl.conf`:
  ```ini
  [boot]
  systemd=true
  ```
  Then `wsl --shutdown` and restart. No Docker Desktop licensing question, one
  less moving part.

Either way, build and smoke-test the image locally, but let **CI produce the
published image** so it's built from a clean context.

#### 8.5.7 Release binaries — don't build them locally

PyInstaller produces a binary for the platform it runs on. From WSL you get a
Linux x86-64 binary and nothing else. The macOS and Linux-aarch64 artifacts
promised in §2 **must** come from the GitHub Actions release matrix
(`runs-on: macos-14` for arm64 macOS, `ubuntu-24.04-arm` for Linux aarch64).

Build locally only to smoke-test the spec file. Never attach a locally built
artifact to a release — that's how you ship a binary linked against your dev
box's glibc.

#### 8.5.8 Android SDK for the corpus (M3)

The R8 corpus legs need D8/R8. You do *not* need full Android Studio in WSL:

```bash
# inside the micromamba env (JDK 17 already present)
mkdir -p ~/android-sdk/cmdline-tools && cd ~/android-sdk/cmdline-tools
# fetch commandlinetools-linux-*.zip from developer.android.com, unzip to 'latest/'
export ANDROID_HOME=~/android-sdk
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
sdkmanager --licenses
sdkmanager "build-tools;34.0.0" "platforms;android-34"
```

`d8` and `r8` then live in `$ANDROID_HOME/build-tools/34.0.0/`. Lighter
fallback: pull `r8.jar` straight from Google's maven and invoke it with
`java -cp r8.jar com.android.tools.r8.R8` — fewer moving parts if you only need
the obfuscation matrix and not a full Gradle build.

Pin the build-tools version in the corpus manifest. R8 behavior changes between
versions, and an unpinned R8 makes your benchmark irreproducible — which would
undercut the entire premise of the project.

#### 8.5.9 CI must match

GitHub Actions runners are plain Ubuntu, not WSL, so anything that works in CI
works in WSL — but not always the reverse (paths, line endings, the `/mnt/c`
trap). **Treat CI as the source of truth.** If it passes locally and fails in
CI, CI is right.

Use `mamba-org/setup-micromamba@v1` in the workflow with the same
`environment.yml`, so local and CI resolve identically.

---

## 9. Milestones

Each has an exit criterion that is a command. Estimates assume focused evenings,
not full days.

### M0 — Scaffold and guardrails (1–2 days)

Everything that makes later work non-negotiable. Do this first; it's boring and
it's the reason the project won't rot.

- `git init`, git identity configured, `.githooks/commit-msg` installed
- `pyproject.toml` (uv), `src/protoloom/` layout, `typer` CLI stub
- `scripts/no_docstrings.py`, ruff, mypy strict, import-linter contracts
- CI: test + lint + no-docstrings + **author check** + import-linter
- `LICENSE` (Apache-2.0 — matches the protobuf ecosystem and is friendlier than
  GPL for corporate RE teams, which is your actual audience; note pbtk is GPL,
  so this is also a real differentiator for anyone at a company)
- `SECURITY.md`, `CONTRIBUTING.md` with the commit convention
- `model.py` dataclasses, fully typed, with tests

**Exit:** `make check` passes on an empty implementation. CI green. A commit
containing a `Co-Authored-By` trailer is rejected locally *and* by CI (test it
deliberately, then delete the test commit).

### M1 — Container layer (2–3 days)

Format detection, APK/zip walking, ELF/Mach-O section access, minimal DEX reader
(header, string_ids, type_ids, method_ids, class_defs, code_items).

**Exit:** `protoloom inspect <file>` correctly identifies and inventories APK,
DEX, ELF, Mach-O, and Go binaries. DEX reader round-trips the string pool of a
real APK against `androguard`'s output (use androguard **as a test oracle only**,
not a runtime dep).

### M2 — Path 1, descriptor extraction (2–3 days)

The fast win. Scan, validate, dedupe, resolve, emit.

**Exit:** on a `protoc --cpp_out` binary and a `protoc-gen-go` binary built from
a known `.proto`, `protoloom extract` recovers a schema that is **byte-identical**
to the original `FileDescriptorProto`. Emitted `.proto` compiles.

### M3 — Benchmark harness, before the hard extractor (3–4 days)

Corpus manifest + fetch + hash verify; compilation matrix driver; all metrics in
section 6 with unit tests on hand-constructed cases; `protoloom bench` and
`--per-target`; `benchmarks/results.md` initialized.

**Exit:** `protoloom bench --corpus tier-a-small` runs end to end and reports
numbers for the Path-1-only tool. Those numbers are committed as the baseline.
The metrics have tests proving they return known values on hand-built inputs.

*Sequencing note: this lands before the hard extractor on purpose. You cannot
fool yourself about the info-string decoder's accuracy if the scoreboard already
exists and you didn't build it while staring at your own output.*

### M4 — Path 2, protobuf-lite (1–2 weeks — the real work)

Info-string decoder against pinned upstream source; DEX dataflow for
`newMessageInfo` operands; objects-array layout sniffing; field type mapping;
name normalization; obfuscation detection; bail-out counter.

**Exit:** field recall ≥90% and wire-type accuracy ≥95% on the unobfuscated
javalite corpus leg. `protoc` compile rate 100%. Round-trip rate ≥90%. Bail-out
count printed and under 1%. **Ship `v0.1.0` to PyPI here — tag it, publish it,
don't wait for perfect.**

### M5 — Tier B and C validation (1 week)

Real-app manifest + fetch script; run against 8–12 real APKs; diff against
upstream `.proto`; round-trip harness with real captured payloads; Docker image.

**Exit:** `benchmarks/results.md` has a real-app table with per-app numbers,
including the ones that went badly, root-caused. This is the section people will
quote.

### M6 — Reconciliation, emission polish, dashboard (4–5 days)

Multi-path merge, conflict recording, confidence propagation, `.desc` output,
`recovery.json`, `report.md`, dashboard, `doctor`, demo command.

**Exit:** `protoloom demo` runs with zero arguments and produces a verdict line
in under a minute, exactly like `autofte demo`. That command is your best
marketing asset — it's what turns a README skim into an install.

### M7 — Differential study and the launch writeup (1 week)

pbtk comparison, R8 cliff study, ceiling analysis, per-target diagnosis scripts,
README rewrite with the full results tables.

**Exit:** `v0.2.0` tagged with binaries attached. README has the comparison table
and the honest floor. Launch posts go out.

**Total: roughly 6–8 weeks of evenings.** M4 is the risk; everything else is
tractable.

---

## 10. Risk register — what to do when something breaks

You asked for the "if this fails, what's next" map. This is it.

| # | Risk | Signal you'll see | Primary mitigation | Fallback | Last resort |
|---|---|---|---|---|---|
| 1 | Info-string format differs across protobuf versions more than expected | Decoder works on 3.21, garbage on 4.x | Version-detect from the header block; keep a decoder per format generation, selected at runtime | Detect-and-refuse with a clear message + bail-out counter, rather than wrong output | Scope v1 to the versions you handle, state it plainly in the README. A tool that covers 80% honestly beats one that covers 100% wrongly |
| 2 | R8 obliterates the `newMessageInfo` pattern (inlining, string encryption) | Call sites not found in real APKs | Match on the `dynamicMethod` switch structure rather than the method name; strings survive inlining | `--jadx` fallback for decompiled context | Report it as a measured coverage limit and quantify it. "R8 aggressive mode defeats static recovery in N% of cases" is a *publishable finding*, not a failure |
| 3 | DEX dataflow too fragile for real bytecode | High bail-out counter on real apps | Widen the opcode whitelist incrementally, driven by the bail-out log — let real failures drive the work | Fall back to a linear scan heuristic (nearest preceding `const-string`) marked `medium` confidence | `androguard` as an optional heavy backend |
| 4 | Tier B apps turn out not to use protobuf / not ship `.proto` | Manifest research comes up empty | Widen the candidate pool to F-Droid at large; grep F-Droid source repos for `.proto` + protobuf gradle plugin | Use AOSP components, which definitively ship both | Lean harder on Tier A + C; C is genuinely strong on its own |
| 5 | Descriptor brute-force scan too slow on big binaries | Minutes per file | The `0x0A` + name-shape prefilter from 5.1 | Restrict to `.rodata`/`__const` only | Rust extension for the scan loop |
| 6 | Someone ships a competing tool mid-build | New repo trending | You have the benchmark; they don't. Publish numbers for *both*. The measurement is the moat | Collaborate — offer them the corpus | Ship anyway; two tools with one shared benchmark is a healthy field and you own the benchmark |
| 7 | Scope creep into iOS/Go/C#/wire-inference | M4 stretching past 3 weeks | The scope in 3.1 is fixed. Paths 3–5 are v1.1. Write them in `ROADMAP.md`, not in `src/` | Cut Go from v1 if needed (it's Path 1, so it's nearly free — but cuttable) | Ship Android-lite-only. That's still the gap nobody filled |
| 8 | Type fidelity plateaus below expectations | int32/sint32/uint32 confusion | Compute and publish the theoretical ceiling like AutoFTE's purity ceiling | Report wire-type accuracy as the headline instead, with type fidelity as secondary | Explain the information-theoretic limit. This is a *strength* of your writeup, not a weakness |
| 9 | Nobody notices the launch | Zero stars, again | Section 11. Lead with the benchmark, not the tool | Direct outreach: pbtk author, ghidriff author, the IOActive gRPC post author | Talk submission — this is a solid con talk (see 11.4) |
| 11 | WSL networking blocks live traffic capture (Tier C) | mitmproxy can't see emulator traffic | `networkingMode=mirrored` + emulator on Windows host (§8.5.5) | Run the whole capture leg on Windows, copy flow dumps into WSL | Ship Tier A round-trip numbers only and document the gap. Tier A alone is publishable |
| 12 | WSL 9p filesystem makes benchmark runs unusably slow | `protoloom bench` takes 10x expected | Repo lives on the Linux filesystem, never `/mnt/c` (§8.5.1) | Move the corpus cache to `~/.cache/protoloom` explicitly | Run benchmarks in CI or a cloud box |
| 10 | An agent silently vibe-codes a wrong parser | Tests pass, real APKs fail | Section 0.4's three defenses; the bail-out counter; property tests | Diff against pbtk on shared cases — disagreement is a bug report | Rewrite the decoder from upstream source with a human reading along |

---

## 11. The blog and the story

You want every repo to become a story. Here's the structure that works, and the
specific reason it works for *you*: your differentiator is that you measure
things and publish inconvenient numbers. A repo shows the result. A blog series
shows the *reasoning*, which is the thing a Google interviewer is actually
trying to assess and cannot get from a README.

Write posts **as you go**, not retrospectively. Retrospective posts are tidy and
lifeless; contemporaneous ones contain the wrong turns, which is the interesting
part. Keep `docs/blog/NN-slug.md` in the repo, drafted at the end of each
milestone, then publish.

### 11.1 The series (8 posts, mapped to milestones)

1. **"Every protobuf schema recovery tool, and why none of them have a number"**
   *(after M0)* — The landscape survey. pbtk, reprotobuf, androproto, the
   gists. State the thesis: this field has no benchmark, so nobody knows what
   works. Announce you're building the benchmark first. **This post is
   shareable before you've written a line of extractor**, and it recruits
   interest early.
2. **"Reading a DEX file without a decompiler"** *(M1)* — Format walkthrough.
   Broadly useful beyond this project; will pick up search traffic forever.
3. **"The schema is still in the binary"** *(M2)* — Path 1. Very visual: show
   the raw bytes, show the recovered `.proto`. Satisfying, easy read.
4. **"Building the scoreboard before the thing it scores"** *(M3)* — The
   methodology post. This is the one that hiring managers respect. Metrics,
   ceilings, why macro and micro both.
5. **"Decoding protobuf-lite info strings"** *(M4)* — The deep technical
   centerpiece. This is the post that becomes the reference nobody has written.
6. **"What R8 actually destroys"** *(M5)* — The measured obfuscation cliff on
   real shipping apps. Original research. Genuinely novel data.
7. **"Round-tripping real traffic"** *(M5/M6)* — Validation without ground
   truth. Conceptually interesting to a wide audience.
8. **"Where PROTOLOOM is wrong"** *(M7)* — The honest-floor post. Worst targets,
   root causes, ceilings, what it can't do. **This is your signature move** —
   it's what `diagnose_xmllint_purity.py` is in AutoFTE, and it's the single
   most credibility-generating thing you can publish.

### 11.2 Post structure that works

Open with the concrete problem (a real payload you couldn't read). Show the
failed approach before the working one. Include the number, including when it's
bad. Close with what's still broken. Link the repo at the end, not the top.

Avoid: "In this article we will explore..." Just start with the problem.

### 11.3 Distribution

Post 1 and post 5 to: r/ReverseEngineering, r/netsec, HN (Show HN only when the
tool installs in one line — not before), the Binary Ninja and Ghidra Discords,
lobste.rs. Cross-post to your blog canonically with `rel=canonical`.

Tag the pbtk author on the differential post. Send the IOActive gRPC post author
a note — their guide is the top search result for this problem and it currently
recommends a 2017 tool.

### 11.4 The talk

By M7 you have: novel measured data on a real ecosystem, a working tool, and a
benchmark. That's a conference talk. Realistic targets: BSides (anywhere —
NoVA is in your backyard), Objective by the Sea, ShmooCon, RECon, Hack in the
Box. A talk is worth more for the Google application than three more repos.

---

## 12. What to do this week

In order. Don't skip ahead.

1. **Tag AutoFTE `v0.1.0` and push it.** Everything is wired. This is one
   evening and it converts a finished project into a usable one. It should not
   wait for PROTOLOOM.
2. Verify the `protoloom` name is free on PyPI and GitHub (section 1).
3. Do the Tier B research — spend two hours confirming which real Android apps
   ship both protobuf and public `.proto` files. **If this comes back empty, the
   validation story weakens and you should know now, before writing code.** This
   is the single highest-information cheap check in the whole plan.
4. Read `RawMessageInfo.java` and `MessageSchema.newSchemaForRawMessageInfo` at
   HEAD. Pin the SHA. Write notes. This is the technical crux — if it's harder
   than expected, better to learn it in week one.
5. Confirm your WSL setup per §8.5: repo on the Linux filesystem, `wsl --version`
   recent enough for `networkingMode=mirrored`, micromamba root prefix set. Five
   minutes now, saves a weekend at M5.
6. M0 scaffold.
7. Draft blog post 1 while the landscape research is fresh in your head.

---

## 13. Open questions to resolve before M4

- Which protobuf runtime versions to support in v1? (Decide after step 4 above.)
- Does the Kotlin protobuf-lite codegen differ from Java's in info-string
  emission? (Test early — Kotlin adoption in Android is majority now, so this
  is not an edge case.)
- Do any Tier B candidates use protobuf **Java full** rather than lite? Those
  are Path 1 targets and give you easy wins for the real-app table.
- Is there an AAB (Android App Bundle) split-APK case that hides DEX in a
  feature module? Handle or explicitly scope out.
- License call: Apache-2.0 recommended (section M0) — confirm you're happy that
  corporate RE teams can use it freely, since that's the audience that will
  actually adopt it.