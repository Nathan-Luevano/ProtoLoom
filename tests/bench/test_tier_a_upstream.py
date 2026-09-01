import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from protoloom.bench.upstream import (
    download,
    extract,
    validate_source_manifest,
)


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str = "https://example.test/archive") -> None:
        super().__init__(data)
        self.url = url

    def geturl(self) -> str:
        return self.url


def _manifest() -> dict[str, Any]:
    return {
        "name": "sample",
        "sources": [
            {
                "name": "upstream",
                "commit": "a" * 40,
                "url": "https://example.test/archive",
                "sha256": "b" * 64,
                "size": 12,
                "includes": ["src", "."],
                "targets": [{"name": "sample", "proto": "sample.proto"}],
            }
        ],
    }


def test_manifest_refuses_unsafe_proto_path() -> None:
    manifest = _manifest()
    manifest["sources"][0]["targets"][0]["proto"] = "../sample.proto"
    with pytest.raises(ValueError, match="unsafe target proto"):
        validate_source_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("name", "../upstream", "source name"),
        ("commit", "abc", "full commit SHA"),
        ("files", [], "non-empty array"),
        ("includes", [], "include roots"),
        ("targets", [], "needs targets"),
    ],
)
def test_manifest_refuses_incomplete_or_unsafe_sources(
    field: str, value: object, error: str
) -> None:
    manifest = _manifest()
    manifest["sources"][0][field] = value
    with pytest.raises(ValueError, match=error):
        validate_source_manifest(manifest)


def test_manifest_refuses_unsupported_compiled_leg() -> None:
    manifest = _manifest()
    manifest["sources"][0]["targets"][0]["compiled_leg"] = "go-binary"
    with pytest.raises(ValueError, match="unsupported compiled leg"):
        validate_source_manifest(manifest)


def test_download_checks_redirect_size_hash_and_installs_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"pinned archive"
    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(payload),
    )
    destination = tmp_path / "archive.tar.gz"
    download(
        "https://example.test/archive",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        destination,
    )
    assert destination.read_bytes() == payload
    assert not destination.with_name(destination.name + ".part").exists()


def test_download_refuses_non_https_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"archive"
    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(payload, "http://example.test/archive"),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        download(
            "https://example.test/archive",
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            tmp_path / "archive.tar.gz",
        )


def test_download_refuses_cache_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "archive.tar.gz"
    destination.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        download("https://example.test/archive", "0" * 64, 1, destination)


def test_extract_refuses_traversal_and_links(tmp_path: Path) -> None:
    for name, kind in (("../escape", tarfile.REGTYPE), ("root/link", tarfile.SYMTYPE)):
        archive = tmp_path / f"{kind!s}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 0
            if kind == tarfile.SYMTYPE:
                member.linkname = "target"
            bundle.addfile(member, io.BytesIO())
        with pytest.raises(ValueError, match=r"unsafe|non-file"):
            extract(archive, tmp_path / f"out-{kind!s}")
