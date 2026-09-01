import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from protoloom.bench.upstream import (
    download,
    extract,
    materialize_source,
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


@pytest.mark.parametrize(
    ("size", "digest", "error"),
    [
        (3, hashlib.sha256(b"payload").hexdigest(), "exceeds pinned size"),
        (8, hashlib.sha256(b"payload").hexdigest(), "size mismatch"),
        (7, "0" * 64, "hash mismatch"),
    ],
)
def test_download_cleans_partial_on_pin_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    digest: str,
    error: str,
) -> None:
    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(b"payload"),
    )
    destination = tmp_path / "archive.tar.gz"
    with pytest.raises(ValueError, match=error):
        download("https://example.test/archive", digest, size, destination)
    assert not destination.with_name(destination.name + ".part").exists()


def test_download_refuses_cache_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "archive.tar.gz"
    destination.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        download("https://example.test/archive", "0" * 64, 1, destination)


def test_download_reuses_verified_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"cached"
    destination = tmp_path / "archive.tar.gz"
    destination.write_bytes(payload)
    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: pytest.fail("network should not be used"),
    )
    download(
        "https://example.test/archive",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        destination,
    )
    assert destination.read_bytes() == payload


def test_download_refuses_invalid_size_and_partial_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "archive.tar.gz"
    with pytest.raises(ValueError, match="128 MiB limit"):
        download("https://example.test/archive", "0" * 64, 0, destination)
    partial = destination.with_name(destination.name + ".part")
    partial.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="partial cache path is a symlink"):
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


def test_extract_materializes_one_root_tree(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        directory = tarfile.TarInfo("source/protos")
        directory.type = tarfile.DIRTYPE
        bundle.addfile(directory)
        payload = b'syntax = "proto3";\n'
        member = tarfile.TarInfo("source/protos/sample.proto")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    root = extract(archive, tmp_path / "unpacked")

    assert root == tmp_path / "unpacked/source"
    assert (root / "protos/sample.proto").read_bytes() == payload


def test_extract_refuses_multiple_roots(tmp_path: Path) -> None:
    archive = tmp_path / "multiple.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("first/a.proto", "second/b.proto"):
            member = tarfile.TarInfo(name)
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="one root directory"):
        extract(archive, tmp_path / "multiple")


def test_materialize_source_downloads_individual_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads: list[tuple[str, Path]] = []

    def fake_download(url: str, expected: str, size: int, output: Path) -> None:
        downloads.append((url, output))
        output.write_bytes(expected.encode())

    monkeypatch.setattr("protoloom.bench.upstream.download", fake_download)
    root = tmp_path / "source"
    source = {
        "files": [
            {
                "path": "proto/one.proto",
                "url": "https://example.test/one",
                "sha256": "a" * 64,
                "size": 1,
            }
        ]
    }

    result = materialize_source(source, tmp_path / "cache", root)

    assert result == root
    assert downloads == [("https://example.test/one", root / "proto/one.proto")]
    assert (root / "proto/one.proto").read_text() == "a" * 64


def test_materialize_source_downloads_and_extracts_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, expected: str, size: int, output: Path) -> None:
        calls.append((url, output))

    def fake_extract(archive: Path, destination: Path) -> Path:
        calls.append(("extract", archive))
        return destination / "source-root"

    monkeypatch.setattr("protoloom.bench.upstream.download", fake_download)
    monkeypatch.setattr("protoloom.bench.upstream.extract", fake_extract)
    source = {
        "name": "protobuf",
        "commit": "a" * 40,
        "url": "https://example.test/source.tar.gz",
        "sha256": "b" * 64,
        "size": 10,
    }
    cache = tmp_path / "cache"
    root = tmp_path / "sources"

    result = materialize_source(source, cache, root)

    archive = cache / f"protobuf-{'a' * 40}.tar.gz"
    assert result == root / "source-root"
    assert calls == [(source["url"], archive), ("extract", archive)]
