import json
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


def test_manifest_wraps_json_read_failures(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b"\xff")

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


def test_hash_rejects_oversized_cached_artifact(tmp_path: Path) -> None:
    path = tmp_path / "cached.bin"
    path.write_bytes(b"large")

    with pytest.raises(CorpusError, match="artifact exceeds 4 bytes"):
        sha256(path, max_size=4)


def test_materialize_bounds_existing_cache_reads(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURES / "manifest.json")
    cache = tmp_path / "cache"
    target = cache / manifest.targets[0].name
    target.mkdir(parents=True)
    (target / manifest.targets[0].truth.name).write_bytes(b"large")

    with pytest.raises(CorpusError, match="artifact exceeds 4 bytes"):
        materialize(manifest, cache, max_artifact_size=4)


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
