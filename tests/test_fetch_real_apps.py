import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def load_fetcher() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "fetch_real_apps.py"
    spec = importlib.util.spec_from_file_location("fetch_real_apps", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fetch_real_apps.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetcher = load_fetcher()
fetch_app = fetcher.fetch_app
load_manifest = fetcher.load_manifest
sha256_file = fetcher.sha256_file


def write_manifest(path: Path, app: dict[str, object]) -> None:
    path.write_text(json.dumps({"version": 1, "apps": [app]}), encoding="utf-8")


def app(sha256: str = "0" * 64, size: int = 3) -> dict[str, object]:
    return {
        "id": "sample-app",
        "artifact": {
            "url": "https://example.invalid/app.apk",
            "sha256": sha256,
            "size": size,
        },
    }


def test_repository_manifest_is_valid() -> None:
    apps = load_manifest(Path("benchmarks/corpus/tier-b-real-apps.json"))
    assert len(apps) == 8


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"id": "../escape"}, "safe lowercase id"),
        (
            {
                "artifact": {
                    "url": "http://example.test/a",
                    "sha256": "0" * 64,
                    "size": 1,
                }
            },
            "HTTPS",
        ),
        (
            {"artifact": {"url": "https://example.test/a", "sha256": "bad", "size": 1}},
            "SHA-256",
        ),
        (
            {
                "artifact": {
                    "url": "https://example.test/a",
                    "sha256": "0" * 64,
                    "size": 0,
                }
            },
            "size",
        ),
    ],
)
def test_manifest_rejects_unsafe_entries(
    tmp_path: Path, change: dict[str, object], match: str
) -> None:
    candidate = app()
    candidate.update(change)
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, candidate)
    with pytest.raises(ValueError, match=match):
        load_manifest(manifest)


def test_existing_hash_pinned_file_is_reused(tmp_path: Path) -> None:
    payload = b"apk"
    candidate = app(hashlib.sha256(payload).hexdigest(), len(payload))
    target = tmp_path / "sample-app.apk"
    target.write_bytes(payload)
    assert fetch_app(candidate, tmp_path) == target
    assert sha256_file(target) == candidate["artifact"]["sha256"]  # type: ignore[index]


def test_existing_mismatch_is_never_overwritten(tmp_path: Path) -> None:
    candidate = app(hashlib.sha256(b"apk").hexdigest(), 3)
    target = tmp_path / "sample-app.apk"
    target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="does not match"):
        fetch_app(candidate, tmp_path)
    assert target.read_bytes() == b"bad"


def test_symlink_target_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"apk")
    (tmp_path / "sample-app.apk").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        fetch_app(app(hashlib.sha256(b"apk").hexdigest()), tmp_path)
