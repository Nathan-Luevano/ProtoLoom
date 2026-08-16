import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_release_workflow_has_every_distribution_channel() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "softprops/action-gh-release@v2" in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "protoloom-linux-x86_64" in workflow
    assert "protoloom-linux-aarch64" in workflow
    assert "protoloom-macos-arm64" in workflow
    assert "id-token: write" in workflow
    assert "packages: write" in workflow


def test_container_pins_jadx_and_drops_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG JADX_VERSION=" in dockerfile
    assert "ARG JADX_SHA256=" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "USER protoloom" in dockerfile
    assert 'ENTRYPOINT ["protoloom"]' in dockerfile


def test_comparison_harness_records_both_tools(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "sample.dex").write_bytes(b"dex\n039\x00")
    result = subprocess.run(
        [
            ROOT / "scripts/compare_pbtk.sh",
            corpus,
            tmp_path / "results",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PROTOLOOM_BIN": "/bin/true",
            "PBTK_BIN": "/bin/true",
        },
    )
    manifest = (tmp_path / "results/results.tsv").read_text(encoding="utf-8")
    assert "cases=1 protoloom_success=1 pbtk_success=1" in result.stdout
    assert "sample.dex" in manifest
