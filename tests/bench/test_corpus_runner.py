import io
import json
import os
from pathlib import Path

import pytest

from protoloom.bench.corpus import (
    CompilationJob,
    CorpusError,
    drive_compilation_matrix,
    load_manifest,
    materialize,
    sha256,
)
from protoloom.bench.jsonio import read_json
from protoloom.bench.metrics import (
    BenchmarkMessage,
    BenchmarkSchema,
    aggregate_reports,
    score_target,
)
from protoloom.bench.runner import load_schema, render_report, run_corpus

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bench"


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self.url = url

    def geturl(self) -> str:
        return self.url


def test_local_corpus_runs_end_to_end(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    report = run_corpus(manifest, tmp_path)
    output = render_report(report, per_target=True)

    assert len(manifest.variants()) == 4
    assert report.micro["field_precision"] == pytest.approx(5 / 6)
    assert report.micro["round_trip_rate"] == 0.5
    assert "local-descriptor:" in output
    assert "macro" in output and "micro" in output and "lead" in output
    assert "type_fidelity_ceiling" in output


def test_benchmark_json_reader_bounds_input(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"{} ")

    with pytest.raises(ValueError, match="JSON input exceeds 2 bytes"):
        read_json(path, max_size=2)


def test_benchmark_json_reader_rejects_special_files() -> None:
    with pytest.raises(ValueError, match="JSON input is not a regular file"):
        read_json(Path(os.devnull))


@pytest.mark.parametrize(
    ("payload", "key"),
    [
        ('{"name":"first","name":"second"}', "name"),
        ('{"outer":{"value":1,"value":2}}', "value"),
    ],
)
def test_benchmark_json_reader_rejects_duplicate_keys(
    tmp_path: Path, payload: str, key: str
) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match=f"duplicate JSON key: {key}"):
        read_json(path)


@pytest.mark.parametrize(
    ("payload", "constant"),
    [("NaN", "NaN"), ('{"value":Infinity}', "Infinity"), ("[-Infinity]", "-Infinity")],
)
def test_benchmark_json_reader_rejects_non_finite_numbers(
    tmp_path: Path, payload: str, constant: str
) -> None:
    path = tmp_path / "non-finite.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match=f"non-finite JSON number: {constant}"):
        read_json(path)


@pytest.mark.parametrize("value", ["1e999", "-1e999"])
def test_benchmark_json_reader_rejects_float_overflow(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "overflow.json"
    path.write_text(f'{{"value":{value}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON number exceeds finite range"):
        read_json(path)


def test_benchmark_json_reader_preserves_finite_float(tmp_path: Path) -> None:
    path = tmp_path / "finite.json"
    path.write_text('{"value":1.25}', encoding="utf-8")
    assert read_json(path) == {"value": 1.25}


@pytest.mark.parametrize(
    "value",
    ["1" * 1001, f"0.{'1' * 1001}"],
)
def test_benchmark_json_reader_bounds_number_characters(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "large-number.json"
    path.write_text(f'{{"value":{value}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON number exceeds 1000 characters"):
        read_json(path)


def test_benchmark_json_reader_preserves_bounded_integers(tmp_path: Path) -> None:
    path = tmp_path / "integer.json"
    path.write_text('{"value":-123}', encoding="utf-8")
    assert read_json(path) == {"value": -123}


def test_schema_rejects_non_finite_json_numbers(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text('{"messages":[],"unknown":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite JSON number: NaN"):
        load_schema(path)


def test_manifest_wraps_non_finite_json_numbers(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"name":NaN,"targets":[]}', encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid corpus manifest"):
        load_manifest(path)


def test_manifest_wraps_float_overflow(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"name":1e999,"targets":[]}', encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid corpus manifest"):
        load_manifest(path)


def test_manifest_wraps_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"name":"first","name":"second"}', encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid corpus manifest"):
        load_manifest(path)


def test_schema_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text('{"compiled":true,"compiled":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key: compiled"):
        load_schema(path)


def test_manifest_wraps_json_read_failures(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"\xff")

    with pytest.raises(CorpusError, match="invalid corpus manifest"):
        load_manifest(path)


def test_manifest_wraps_special_file_failure(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.symlink_to(Path(os.devnull))

    with pytest.raises(CorpusError, match="invalid corpus manifest"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("corpus_name", "target_name", "message"),
    [
        ("../escape", "target", "corpus name is unsafe"),
        ("corpus", "../escape", "target name is unsafe"),
        ("corpus", "bad\nname", "target name is unsafe"),
    ],
)
def test_manifest_rejects_unsafe_cache_names(
    tmp_path: Path, corpus_name: str, target_name: str, message: str
) -> None:
    truth = {"name": "truth.json", "path": "truth.json", "sha256": "0" * 64}
    recovered = {
        "name": "recovered.json",
        "path": "recovered.json",
        "sha256": "0" * 64,
    }
    payload = {
        "name": corpus_name,
        "targets": [{"name": target_name, "truth": truth, "recovered": recovered}],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusError, match=message):
        load_manifest(path)


def test_compilation_matrix_driver_visits_every_variant() -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    visited: list[CompilationJob] = []
    drive_compilation_matrix(manifest, visited.append)
    assert len(visited) == 4
    assert {job.variant["runtime"] for job in visited} == {"cpp", "go"}


def test_manifest_bounds_compilation_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("protoloom.bench.corpus.MAX_COMPILATION_JOBS", 3)

    with pytest.raises(CorpusError, match="matrix expands beyond 3 jobs"):
        load_manifest(path)


def test_manifest_rejects_duplicate_matrix_values(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["matrix"] = {"runtime": ["go", "go"]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusError, match="axis values must be unique"):
        load_manifest(path)


@pytest.mark.parametrize("location", ["manifest", "target", "truth", "recovered"])
def test_manifest_rejects_unknown_fields(tmp_path: Path, location: str) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    if location == "manifest":
        value = payload
    elif location == "target":
        value = payload["targets"][0]
    else:
        value = payload["targets"][0][location]
    value["unexpected"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="unknown field: unexpected"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("location", "value"),
    [("axis", "bad\naxis"), ("axis", "x" * 256), ("value", "../escape")],
)
def test_manifest_rejects_unsafe_matrix_names(
    tmp_path: Path, location: str, value: str
) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["matrix"] = {value if location == "axis" else "runtime": [value]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match=f"matrix {location} name is unsafe"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("location", "message"),
    [
        ("corpus", "corpus name must be a string"),
        ("target", "target name must be a string"),
        ("hash", "artifact SHA-256 must be a string"),
        ("path", "artifact path must be a string"),
        ("matrix", "matrix value must be a string"),
    ],
)
def test_manifest_rejects_non_string_values(
    tmp_path: Path, location: str, message: str
) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    if location == "corpus":
        payload["name"] = 1
    elif location == "target":
        payload["targets"][0]["name"] = 1
    elif location == "hash":
        payload["targets"][0]["truth"]["sha256"] = 1
    elif location == "path":
        payload["targets"][0]["truth"]["path"] = 1
    else:
        payload["matrix"] = {"runtime": [1]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CorpusError, match=message):
        load_manifest(path)


@pytest.mark.parametrize(
    "url",
    ["http://example.test/artifact", "file:///etc/passwd", "https://user@host/file"],
)
def test_manifest_rejects_unsafe_artifact_urls(tmp_path: Path, url: str) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    artifact = payload["targets"][0]["truth"]
    artifact.pop("path")
    artifact["url"] = url
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="unauthenticated HTTPS"):
        load_manifest(path)


def test_materialize_refuses_non_https_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    artifact = payload["targets"][0]["truth"]
    artifact.pop("path")
    artifact["url"] = "https://example.test/artifact"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "protoloom.bench.corpus.urllib.request.urlopen",
        lambda url, timeout: Response(b"{}", "http://example.test/artifact"),
    )
    with pytest.raises(CorpusError, match="unauthenticated HTTPS"):
        materialize(load_manifest(path), tmp_path / "cache")
    assert not tuple((tmp_path / "cache").rglob(".*"))


def test_hash_mismatch_removes_bad_download(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    manifest_path.write_text(
        '{"name":"bad","targets":[{"name":"target",'
        '"truth":{"name":"truth.json","path":"source.json",'
        '"sha256":"0000000000000000000000000000000000000000000000000000000000000000"},'
        '"recovered":{"name":"recovered.json","path":"source.json",'
        '"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}}]}',
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    with pytest.raises(CorpusError, match="SHA-256 mismatch"):
        materialize(manifest, tmp_path / "cache")
    assert not (tmp_path / "cache" / "target" / "truth.json").exists()


def test_materialize_rejects_symlinked_target_cache(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    cache = tmp_path / "cache"
    cache.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (cache / manifest.targets[0].name).symlink_to(victim, target_is_directory=True)

    with pytest.raises(CorpusError, match="target cache is a symlink"):
        materialize(manifest, cache)

    assert list(victim.iterdir()) == []


def test_materialize_rejects_oversized_artifact(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    cache = tmp_path / "cache"

    with pytest.raises(CorpusError, match="artifact exceeds 2 bytes"):
        materialize(manifest, cache, max_artifact_size=2)

    assert not tuple(cache.rglob("*.part"))


def test_materialize_syncs_artifacts_and_cache_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    resolved = materialize(manifest, tmp_path / "cache")
    assert len(resolved) == 2
    assert len(synced) == 4
    assert not tuple((tmp_path / "cache").rglob(".*"))


def test_hash_rejects_oversized_cached_artifact(tmp_path: Path) -> None:
    path = tmp_path / "cached.bin"
    path.write_bytes(b"large")

    with pytest.raises(CorpusError, match="artifact exceeds 4 bytes"):
        sha256(path, max_size=4)


def test_hash_rejects_special_files() -> None:
    with pytest.raises(CorpusError, match="artifact is not a regular file"):
        sha256(Path(os.devnull))


def test_materialize_bounds_existing_cache_reads(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    cache = tmp_path / "cache"
    target = cache / manifest.targets[0].name
    target.mkdir(parents=True)
    (target / manifest.targets[0].truth.name).write_bytes(b"large")

    with pytest.raises(CorpusError, match="artifact exceeds 4 bytes"):
        materialize(manifest, cache, max_artifact_size=4)


def test_artifact_rejects_two_sources() -> None:
    from protoloom.bench.corpus import Artifact

    with pytest.raises(CorpusError, match="needs exactly one source"):
        Artifact(name="a", sha256="0" * 64, path="a", url="https://example.test/a")


def test_artifact_rejects_invalid_sha256() -> None:
    from protoloom.bench.corpus import Artifact

    with pytest.raises(CorpusError, match="invalid SHA-256"):
        Artifact(name="a", sha256="not-hex", path="a")


def test_target_rejects_duplicate_artifact_names() -> None:
    from protoloom.bench.corpus import Artifact, CorpusTarget

    artifact = Artifact(name="same.json", sha256="0" * 64, path="same.json")
    with pytest.raises(CorpusError, match="duplicate artifact names"):
        CorpusTarget(name="target", truth=artifact, recovered=artifact)


def test_manifest_dataclass_rejects_empty_targets() -> None:
    from protoloom.bench.corpus import CorpusManifest

    with pytest.raises(CorpusError, match="at least one target"):
        CorpusManifest(name="empty", targets=(), matrix={}, root=Path("."))


def test_manifest_dataclass_rejects_duplicate_target_names() -> None:
    from protoloom.bench.corpus import Artifact, CorpusManifest, CorpusTarget

    truth = Artifact(name="truth.json", sha256="0" * 64, path="truth.json")
    recovered = Artifact(name="recovered.json", sha256="1" * 64, path="recovered.json")
    target = CorpusTarget(name="dup", truth=truth, recovered=recovered)
    with pytest.raises(CorpusError, match="target names must be unique"):
        CorpusManifest(name="c", targets=(target, target), matrix={}, root=Path("."))


def test_manifest_dataclass_rejects_too_many_matrix_axes() -> None:
    from protoloom.bench.corpus import Artifact, CorpusManifest, CorpusTarget

    truth = Artifact(name="truth.json", sha256="0" * 64, path="truth.json")
    recovered = Artifact(name="recovered.json", sha256="1" * 64, path="recovered.json")
    target = CorpusTarget(name="t", truth=truth, recovered=recovered)
    matrix = {f"axis{i}": ("v",) for i in range(33)}
    with pytest.raises(CorpusError, match="more than 32 axes"):
        CorpusManifest(name="c", targets=(target,), matrix=matrix, root=Path("."))


def test_manifest_dataclass_rejects_empty_matrix_axis() -> None:
    from protoloom.bench.corpus import Artifact, CorpusManifest, CorpusTarget

    truth = Artifact(name="truth.json", sha256="0" * 64, path="truth.json")
    recovered = Artifact(name="recovered.json", sha256="1" * 64, path="recovered.json")
    target = CorpusTarget(name="t", truth=truth, recovered=recovered)
    with pytest.raises(CorpusError, match="matrix axes cannot be empty"):
        CorpusManifest(
            name="c", targets=(target,), matrix={"runtime": ()}, root=Path(".")
        )


def test_manifest_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CorpusError, match="manifest root must be an object"):
        load_manifest(path)


def test_manifest_rejects_empty_targets_list(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"name": "c", "targets": []}', encoding="utf-8")
    with pytest.raises(CorpusError, match="at least one target"):
        load_manifest(path)


def test_manifest_rejects_duplicate_target_names_from_json(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["targets"] = [payload["targets"][0], payload["targets"][0]]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="target names must be unique"):
        load_manifest(path)


def test_manifest_rejects_non_object_matrix(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["matrix"] = []
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="matrix must be an object"):
        load_manifest(path)


def test_manifest_rejects_empty_matrix_axis_from_json(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["matrix"] = {"runtime": []}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="matrix axes cannot be empty"):
        load_manifest(path)


def test_materialize_rejects_non_positive_artifact_size(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    with pytest.raises(ValueError, match="maximum artifact size must be positive"):
        materialize(manifest, tmp_path / "cache", max_artifact_size=0)


def test_materialize_rejects_symlinked_destination(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    real = tmp_path / "real"
    real.mkdir()
    cache = tmp_path / "cache"
    cache.symlink_to(real, target_is_directory=True)
    with pytest.raises(CorpusError, match="corpus cache is a symlink"):
        materialize(manifest, cache)


def test_materialize_rejects_symlinked_artifact_output(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    cache = tmp_path / "cache"
    target_root = cache / manifest.targets[0].name
    target_root.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    (target_root / manifest.targets[0].truth.name).symlink_to(victim)

    with pytest.raises(CorpusError, match="artifact cache is a symlink"):
        materialize(manifest, cache)


def test_hash_rejects_non_positive_max_size(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"data")
    with pytest.raises(ValueError, match="maximum artifact size must be positive"):
        sha256(path, max_size=0)


def test_hash_enforces_bound_during_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"x" * 10)
    real_fstat = os.fstat

    class FakeStat:
        def __init__(self, real: os.stat_result) -> None:
            self._real = real
            self.st_size = 5

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    monkeypatch.setattr(
        "protoloom.bench.corpus.os.fstat", lambda fd: FakeStat(real_fstat(fd))
    )
    with pytest.raises(CorpusError, match="artifact exceeds 5 bytes"):
        sha256(path, max_size=5)


def test_copy_artifact_rejects_path_escaping_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    root = tmp_path / "corpus_root"
    root.mkdir()
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "escape",
                "targets": [
                    {
                        "name": "target",
                        "truth": {
                            "name": "truth.json",
                            "path": "../outside.json",
                            "sha256": "0" * 64,
                        },
                        "recovered": {
                            "name": "recovered.json",
                            "path": "../outside.json",
                            "sha256": "0" * 64,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    with pytest.raises(CorpusError, match="artifact path escapes corpus root"):
        materialize(manifest, tmp_path / "cache")


def test_materialize_downloads_valid_https_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    artifact = payload["targets"][0]["truth"]
    artifact.pop("path")
    artifact["url"] = "https://example.test/artifact"
    content = (FIXTURES / "truth.json").read_bytes()
    import hashlib

    artifact["sha256"] = hashlib.sha256(content).hexdigest()
    (tmp_path / "recovered.json").write_bytes(
        (FIXTURES / "recovered.json").read_bytes()
    )
    payload["targets"][0]["recovered"]["path"] = "recovered.json"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "protoloom.bench.corpus.urllib.request.urlopen",
        lambda url, timeout: Response(content, "https://example.test/artifact"),
    )
    resolved = materialize(load_manifest(path), tmp_path / "cache")
    key = f"{payload['targets'][0]['name']}/truth.json"
    assert resolved[key].read_bytes() == content


def test_target_rejects_non_object(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["targets"] = [1]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="target must be an object"):
        load_manifest(path)


def test_mapping_rejects_non_object(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["targets"][0]["truth"] = "not-a-mapping"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="truth must be an object"):
        load_manifest(path)


def test_as_list_rejects_non_array_matrix_value(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    payload["matrix"] = {"runtime": "go"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="matrix value must be an array"):
        load_manifest(path)


def test_report_renders_unmeasured_metrics_as_na() -> None:
    schema = BenchmarkSchema((BenchmarkMessage("Empty", ()),))
    report = aggregate_reports([score_target("empty", schema, schema)])
    rendered = render_report(report, per_target=True)

    assert "type_fidelity                   n/a       n/a" in rendered
    assert "type_fidelity=n/a" in rendered
    assert "round_trip_rate                 n/a       n/a" in rendered
    assert "round_trip_rate=n/a" in rendered
    assert "type_fidelity_ceiling           n/a       n/a" in rendered
    assert "type_fidelity_ceiling=n/a" in rendered


def test_schema_loads_explicit_exact_type_evidence(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(
        '{"messages": [], "type_fidelity_ambiguities": []}', encoding="utf-8"
    )

    assert load_schema(path).type_fidelity_ambiguities == ()


def test_schema_rejects_invalid_type_ambiguities(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(
        '{"messages": [], "type_fidelity_ambiguities": ["int32"]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ambiguity groups"):
        load_schema(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [{"fields": [{}, {}]}]},
        {"messages": [{"enums": [{"values": [[], []]}]}]},
        {"enums": [{"values": [[], []]}]},
    ],
)
def test_schema_bounds_total_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("protoloom.bench.runner.MAX_BENCH_SCHEMA_ITEMS", 2)

    with pytest.raises(ValueError, match="benchmark schema exceeds 2 items"):
        load_schema(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"compiled": 1}, "compiled must be a boolean"),
        ({"round_trip": {"passed": "1"}}, "round_trip.passed must be an integer"),
        ({"round_trip": {"total": True}}, "round_trip.total must be an integer"),
        ({"messages": [{"name": 1}]}, "message name must be a string or null"),
        ({"messages": [{"parent": False}]}, "message parent must be a string or null"),
    ],
)
def test_schema_rejects_coerced_metadata(
    tmp_path: Path, payload: object, message: str
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_schema(path)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (
            {"number": "1", "name": "x", "proto_type": "int32", "wire_type": 0},
            "field number must be an integer",
        ),
        (
            {"number": 1, "name": 2, "proto_type": "int32", "wire_type": 0},
            "field name must be a string",
        ),
        (
            {"number": 1, "name": "x", "proto_type": "int32", "wire_type": 6},
            "field wire_type is invalid",
        ),
        (
            {
                "number": 1,
                "name": "x",
                "proto_type": "int32",
                "wire_type": 0,
                "label": "many",
            },
            "field label is invalid",
        ),
    ],
)
def test_schema_rejects_invalid_fields(
    tmp_path: Path, field: object, message: str
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({"messages": [{"fields": [field]}]}), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_schema(path)


@pytest.mark.parametrize("value", ["READY", ["READY"], ["READY", "1"]])
def test_schema_rejects_invalid_enum_values(tmp_path: Path, value: object) -> None:
    path = tmp_path / "schema.json"
    payload = {"enums": [{"name": "State", "values": [value]}]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="enum value"):
        load_schema(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"messages": [{"name": "A"}, {"name": "A"}]}, "message names"),
        ({"enums": [{"name": "E"}, {"name": "E"}]}, "enum names"),
        (
            {
                "messages": [
                    {
                        "fields": [
                            {
                                "number": 1,
                                "name": "a",
                                "proto_type": "int32",
                                "wire_type": 0,
                            },
                            {
                                "number": 1,
                                "name": "b",
                                "proto_type": "int32",
                                "wire_type": 0,
                            },
                        ]
                    }
                ]
            },
            "field numbers",
        ),
        (
            {"enums": [{"name": "E", "values": [["A", 0], ["A", 1]]}]},
            "enum value names",
        ),
    ],
)
def test_schema_rejects_ambiguous_duplicates(
    tmp_path: Path, payload: object, message: str
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{message} must be unique"):
        load_schema(path)


@pytest.mark.parametrize(
    "ambiguities",
    [
        '[["int32", "int32"]]',
        '[["int32", "uint32"], ["uint32", "sint32"]]',
    ],
)
def test_schema_rejects_overlapping_type_ambiguities(
    tmp_path: Path, ambiguities: str
) -> None:
    path = tmp_path / "schema.json"
    path.write_text(
        f'{{"messages": [], "type_fidelity_ambiguities": {ambiguities}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not overlap"):
        load_schema(path)
