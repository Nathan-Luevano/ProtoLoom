import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def workflow_actions(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return set(re.findall(r"uses:\s*([^@\s]+)@", source))


def version_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    package = project / "src/protoloom"
    scripts = project / "scripts"
    package.mkdir(parents=True)
    scripts.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.0"\n', encoding="utf-8"
    )
    module = package / "__init__.py"
    module.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    script = scripts / "set_version.py"
    shutil.copyfile(ROOT / "scripts/set_version.py", script)
    return script, module


def test_release_workflow_has_every_distribution_channel() -> None:
    path = ROOT / ".github/workflows/release.yml"
    workflow = path.read_text(encoding="utf-8")
    assert {
        "actions/attest",
        "actions/download-artifact",
        "actions/upload-artifact",
        "docker/build-push-action",
        "docker/login-action",
        "docker/metadata-action",
        "docker/setup-buildx-action",
        "docker/setup-qemu-action",
        "pypa/gh-action-pypi-publish",
        "softprops/action-gh-release",
    } <= workflow_actions(path)
    assert "linux/amd64,linux/arm64" in workflow
    assert "protoloom-linux-x86_64" in workflow
    assert "protoloom-linux-aarch64" in workflow
    assert "protoloom-macos-arm64" in workflow
    assert "id-token: write" in workflow
    assert "packages: write" in workflow
    assert workflow.count('scripts/set_version.py "${RELEASE_VERSION#v}"') == 2


def test_ci_workflow_covers_source_and_installed_distributions() -> None:
    path = ROOT / ".github/workflows/ci.yml"
    workflow = path.read_text(encoding="utf-8")

    assert {
        "actions/checkout",
        "astral-sh/setup-uv",
    } <= workflow_actions(path)
    assert 'python-version: "3.11"' in workflow
    assert 'python-version: "3.14"' in workflow
    assert "uv run make check" in workflow
    assert "uv build" in workflow
    assert "protoloom --help" in workflow


def test_runtime_install_includes_fallback_compiler() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert any(item.startswith("grpcio-tools>=") for item in dependencies)


def test_container_pins_jadx_and_drops_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG JADX_VERSION=" in dockerfile
    assert "ARG JADX_SHA256=" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "ARG VERSION=0.1.3" in dockerfile
    assert "USER protoloom" in dockerfile
    assert 'ENTRYPOINT ["protoloom"]' in dockerfile
    assert 'scripts/set_version.py "${VERSION#v}"' in dockerfile


def test_set_version_updates_package_and_runtime_metadata(tmp_path: Path) -> None:
    script, module = version_project(tmp_path)

    subprocess.run(
        [sys.executable, script, "1.2.3rc4"], capture_output=True, check=True
    )

    pyproject = tmp_path / "project/pyproject.toml"
    assert 'version = "1.2.3rc4"' in pyproject.read_text()
    assert module.read_text() == '__version__ = "1.2.3rc4"\n'


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "1.2.3; false", ""])
def test_set_version_rejects_invalid_versions(tmp_path: Path, version: str) -> None:
    script, _ = version_project(tmp_path)

    result = subprocess.run(
        [sys.executable, script, version], capture_output=True, check=False, text=True
    )

    assert result.returncode != 0
    assert "invalid release version" in result.stderr


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
