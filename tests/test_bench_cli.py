from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from protoloom.cli import app

runner = CliRunner()
FIXTURE = Path("tests/fixtures/bench/manifest.json")


def test_version_matches_release() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_bench_accepts_manifest_path(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    result = runner.invoke(app, ["bench", "--corpus", str(FIXTURE), "--per-target"])
    assert result.exit_code == 0, result.output
    assert "field_recall" in result.output
    assert "macro" in result.output and "micro" in result.output
    assert "local-descriptor:" in result.output


def test_bench_rejects_missing_corpus() -> None:
    result = runner.invoke(app, ["bench", "--corpus", "definitely-missing"])
    assert result.exit_code == 2
    assert "corpus does not exist" in result.output


def test_bench_resolves_bundled_corpus_name(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    result = runner.invoke(app, ["bench", "--corpus", "tier-a-small"])
    assert result.exit_code == 0, result.output
    assert "compile_rate" in result.output
