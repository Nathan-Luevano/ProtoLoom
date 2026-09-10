import hashlib
import io
import os
import tarfile
from pathlib import Path
from typing import Any, Self

import pytest

from protoloom.bench.upstream import (
    download,
    extract,
    materialize_source,
    sha256,
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


@pytest.mark.parametrize("manifest", [None, {}, {"sources": {}}])
def test_manifest_requires_sources_array(manifest: object) -> None:
    with pytest.raises(ValueError, match="sources array"):
        validate_source_manifest(manifest)


def test_manifest_requires_source_and_file_objects() -> None:
    with pytest.raises(ValueError, match="source entry must be an object"):
        validate_source_manifest({"sources": ["source"]})
    manifest = _manifest()
    manifest["sources"][0]["files"] = ["file"]
    with pytest.raises(ValueError, match="source file must be an object"):
        validate_source_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("sha256", "short", "SHA-256"),
        ("size", 0, "positive pinned size"),
        ("url", "http://example.test/archive", "HTTPS"),
    ],
)
def test_manifest_requires_valid_remote_pins(
    field: str, value: object, error: str
) -> None:
    manifest = _manifest()
    manifest["sources"][0][field] = value
    with pytest.raises(ValueError, match=error):
        validate_source_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("name", "", "source name"),
        ("name", "bad\nname", "source name"),
        ("commit", "g" * 40, "full commit SHA"),
        ("sha256", "g" * 64, "SHA-256"),
        ("size", True, "positive pinned size"),
        ("url", 1, "URL must be a string"),
    ],
)
def test_manifest_rejects_coerced_source_values(
    field: str, value: object, error: str
) -> None:
    manifest = _manifest()
    manifest["sources"][0][field] = value

    with pytest.raises(ValueError, match=error):
        validate_source_manifest(manifest)


@pytest.mark.parametrize(
    ("location", "error"),
    [("include", "include root"), ("proto", "target proto")],
)
def test_manifest_rejects_non_string_paths(location: str, error: str) -> None:
    manifest = _manifest()
    if location == "include":
        manifest["sources"][0]["includes"] = [1]
    else:
        manifest["sources"][0]["targets"][0]["proto"] = 1

    with pytest.raises(ValueError, match=f"{error} must be a string"):
        validate_source_manifest(manifest)


def test_manifest_refuses_unsafe_file_and_include_paths() -> None:
    manifest = _manifest()
    manifest["sources"][0]["files"] = [
        {
            "path": "../escape.proto",
            "url": "https://example.test/file",
            "sha256": "a" * 64,
            "size": 1,
        }
    ]
    with pytest.raises(ValueError, match="unsafe source file path"):
        validate_source_manifest(manifest)
    manifest = _manifest()
    manifest["sources"][0]["includes"] = ["../escape"]
    with pytest.raises(ValueError, match="unsafe include root"):
        validate_source_manifest(manifest)


@pytest.mark.parametrize("location", ["file", "include", "target"])
@pytest.mark.parametrize("path", ["bad\npath.proto", "x" * 256, "x/" * 2049])
def test_manifest_bounds_and_sanitizes_paths(location: str, path: str) -> None:
    manifest = _manifest()
    if location == "file":
        manifest["sources"][0]["files"] = [
            {
                "path": path,
                "url": "https://example.test/file",
                "sha256": "a" * 64,
                "size": 1,
            }
        ]
    elif location == "include":
        manifest["sources"][0]["includes"] = [path]
    else:
        manifest["sources"][0]["targets"][0]["proto"] = path
    with pytest.raises(ValueError, match="unsafe"):
        validate_source_manifest(manifest)


def test_manifest_rejects_conflicting_source_paths() -> None:
    manifest = _manifest()
    artifact = {
        "url": "https://example.test/file",
        "sha256": "a" * 64,
        "size": 1,
    }
    manifest["sources"][0]["files"] = [
        {**artifact, "path": "proto"},
        {**artifact, "path": "proto/sample.proto"},
    ]
    with pytest.raises(ValueError, match="conflicting source file path"):
        validate_source_manifest(manifest)


def test_manifest_refuses_invalid_and_duplicate_targets() -> None:
    manifest = _manifest()
    manifest["sources"][0]["targets"] = ["target"]
    with pytest.raises(ValueError, match="target entry must be an object"):
        validate_source_manifest(manifest)
    manifest = _manifest()
    target = manifest["sources"][0]["targets"][0]
    manifest["sources"][0]["targets"].append(dict(target))
    with pytest.raises(ValueError, match="duplicate target name"):
        validate_source_manifest(manifest)


def test_manifest_refuses_duplicate_sources() -> None:
    manifest = _manifest()
    manifest["sources"].append(dict(manifest["sources"][0]))
    with pytest.raises(ValueError, match="duplicate source name"):
        validate_source_manifest(manifest)


def test_manifest_bounds_source_count(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    manifest["sources"].append({**manifest["sources"][0], "name": "second"})
    monkeypatch.setattr("protoloom.bench.upstream.MAX_UPSTREAM_SOURCES", 1)

    with pytest.raises(ValueError, match="exceeds 1 sources"):
        validate_source_manifest(manifest)


def test_manifest_rejects_duplicate_source_paths() -> None:
    manifest = _manifest()
    artifact = {
        "path": "proto/sample.proto",
        "url": "https://example.test/sample.proto",
        "sha256": "a" * 64,
        "size": 1,
    }
    manifest["sources"][0]["files"] = [artifact, dict(artifact)]

    with pytest.raises(ValueError, match="duplicate source file path"):
        validate_source_manifest(manifest)


def test_manifest_rejects_duplicate_include_roots() -> None:
    manifest = _manifest()
    manifest["sources"][0]["includes"] = ["src", "src"]

    with pytest.raises(ValueError, match="duplicate include root"):
        validate_source_manifest(manifest)


def test_manifest_bounds_total_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    monkeypatch.setattr("protoloom.bench.upstream.MAX_UPSTREAM_TARGETS", 0)

    with pytest.raises(ValueError, match="exceeds 0 targets"):
        validate_source_manifest(manifest)


def test_manifest_accepts_valid_archive_and_file_sources() -> None:
    archive_manifest = _manifest()
    assert validate_source_manifest(archive_manifest) is archive_manifest
    file_manifest = _manifest()
    file_manifest["sources"][0]["files"] = [
        {
            "path": "proto/sample.proto",
            "url": "https://example.test/sample.proto",
            "sha256": "a" * 64,
            "size": 1,
        }
    ]
    assert validate_source_manifest(file_manifest) is file_manifest


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


def test_download_uses_private_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"pinned archive"
    observed: list[Path] = []

    def respond(request: object, timeout: int) -> Response:
        observed.extend(tmp_path.glob(".archive.tar.gz.*"))
        return Response(payload)

    monkeypatch.setattr("protoloom.bench.upstream.urllib.request.urlopen", respond)
    destination = tmp_path / "archive.tar.gz"
    download(
        "https://example.test/archive",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        destination,
    )
    assert len(observed) == 1
    assert destination.read_bytes() == payload
    assert not observed[0].exists()


def test_download_syncs_file_and_cache_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"pinned archive"
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(payload),
    )
    monkeypatch.setattr(os, "fsync", record_fsync)
    destination = tmp_path / "archive.tar.gz"
    download(
        "https://example.test/archive",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        destination,
    )
    assert destination.read_bytes() == payload
    assert len(synced) == 2


def test_download_closes_directory_after_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"pinned archive"
    real_fsync = os.fsync
    real_close = os.close
    closed: list[int] = []
    sync_count = 0

    def fail_fsync(descriptor: int) -> None:
        nonlocal sync_count
        sync_count += 1
        if sync_count == 2:
            raise OSError("sync failed")
        real_fsync(descriptor)

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(payload),
    )
    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(os, "close", record_close)
    destination = tmp_path / "archive.tar.gz"
    with pytest.raises(OSError, match="sync failed"):
        download(
            "https://example.test/archive",
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            destination,
        )
    assert sync_count == 2
    assert closed


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


def test_upstream_hash_rejects_special_and_oversized_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source is not a regular file"):
        sha256(Path(os.devnull))
    source = tmp_path / "large"
    source.write_bytes(b"large")
    with pytest.raises(ValueError, match="source exceeds 4 bytes"):
        sha256(source, max_size=4)


def test_download_refuses_invalid_size(tmp_path: Path) -> None:
    destination = tmp_path / "archive.tar.gz"
    with pytest.raises(ValueError, match="128 MiB limit"):
        download("https://example.test/archive", "0" * 64, 0, destination)


def test_download_ignores_legacy_partial_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"archive"
    destination = tmp_path / "archive.tar.gz"
    partial = destination.with_name(destination.name + ".part")
    victim = tmp_path / "victim"
    victim.write_bytes(b"preserve")
    partial.symlink_to(victim)
    monkeypatch.setattr(
        "protoloom.bench.upstream.urllib.request.urlopen",
        lambda request, timeout: Response(payload),
    )
    download(
        "https://example.test/archive",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        destination,
    )
    assert destination.read_bytes() == payload
    assert victim.read_bytes() == b"preserve"
    assert partial.is_symlink()


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


@pytest.mark.parametrize(
    "name",
    ["root/bad\nname", f"root/{'x' * 256}", f"root/{'x/' * 2048}item"],
)
def test_extract_refuses_unsafe_member_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    archive.write_bytes(b"archive")
    member = tarfile.TarInfo(name)

    class Bundle:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            yield member

    monkeypatch.setattr(
        "protoloom.bench.upstream.tarfile.open", lambda **kwargs: Bundle()
    )
    with pytest.raises(ValueError, match="unsafe archive member"):
        extract(archive, tmp_path / "output")


def test_extract_refuses_negative_member_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "negative.tar.gz"
    archive.write_bytes(b"archive")
    member = tarfile.TarInfo("root/item")
    member.size = -1

    class Bundle:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            yield member

    monkeypatch.setattr(
        "protoloom.bench.upstream.tarfile.open", lambda **kwargs: Bundle()
    )
    with pytest.raises(ValueError, match="negative size"):
        extract(archive, tmp_path / "output")


def test_extract_refuses_empty_archive(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    with pytest.raises(ValueError, match="empty archive"):
        extract(archive, tmp_path / "empty")


def test_extract_refuses_special_archive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source is not a regular file"):
        extract(Path(os.devnull), tmp_path / "output")


def test_extract_bounds_member_count(tmp_path: Path) -> None:
    archive = tmp_path / "many.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("root/one", "root/two"):
            member = tarfile.TarInfo(name)
            member.size = 0
            bundle.addfile(member, io.BytesIO())

    with pytest.raises(ValueError, match="more than 1 members"):
        extract(archive, tmp_path / "many", max_members=1)


def test_extract_stops_reading_members_at_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"archive")

    class Bundle:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            for name in ("root/one", "root/two"):
                yield tarfile.TarInfo(name)
            raise AssertionError("member limit was not enforced while reading")

    monkeypatch.setattr(
        "protoloom.bench.upstream.tarfile.open", lambda **kwargs: Bundle()
    )
    with pytest.raises(ValueError, match="more than 1 members"):
        extract(archive, tmp_path / "output", max_members=1)


def test_extract_bounds_expanded_size(tmp_path: Path) -> None:
    archive = tmp_path / "large.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("root/large")
        member.size = 5
        bundle.addfile(member, io.BytesIO(b"large"))

    with pytest.raises(ValueError, match="expands beyond 4 bytes"):
        extract(archive, tmp_path / "large", max_size=4)


def test_extract_rejects_symlinked_destination(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.symlink_to(tmp_path / "victim", target_is_directory=True)

    with pytest.raises(ValueError, match="extraction path is a symlink"):
        extract(tmp_path / "missing.tar.gz", destination)


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


def test_extract_syncs_files_and_directory_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.tar.gz"
    payload = b' syntax = "proto3";\n'
    with tarfile.open(archive, "w:gz") as bundle:
        directory = tarfile.TarInfo("source/protos")
        directory.type = tarfile.DIRTYPE
        bundle.addfile(directory)
        member = tarfile.TarInfo("source/protos/sample.proto")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    root = extract(archive, tmp_path / "unpacked")
    assert (root / "protos/sample.proto").read_bytes() == payload
    assert len(synced) == 5


def test_extract_refuses_multiple_roots(tmp_path: Path) -> None:
    archive = tmp_path / "multiple.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("first/a.proto", "second/b.proto"):
            member = tarfile.TarInfo(name)
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "multiple"
    with pytest.raises(ValueError, match="one root directory"):
        extract(archive, destination)
    assert not destination.exists()


def test_extract_refuses_duplicate_normalized_paths(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in ("root/item", "root//item"):
            member = tarfile.TarInfo(name)
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "duplicate"

    with pytest.raises(ValueError, match="duplicate archive member"):
        extract(archive, destination)

    assert not destination.exists()


def test_extract_refuses_nonempty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    existing = destination / "existing"
    existing.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="extraction path already exists"):
        extract(tmp_path / "missing.tar.gz", destination)

    assert existing.read_text(encoding="utf-8") == "preserve"


def test_extract_removes_partial_destination_after_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("root/item")
        member.size = 1
        bundle.addfile(member, io.BytesIO(b"x"))
    monkeypatch.setattr(
        "protoloom.bench.upstream._copy_member",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )
    destination = tmp_path / "destination"

    with pytest.raises(OSError, match="disk full"):
        extract(archive, destination)

    assert not destination.exists()


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
    assert [
        (url, output.relative_to(output.parents[1])) for url, output in downloads
    ] == [("https://example.test/one", Path("proto/one.proto"))]
    assert (root / "proto/one.proto").read_text() == "a" * 64


def test_materialize_source_removes_partial_tree_after_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fail_second_download(url: str, expected: str, size: int, output: Path) -> None:
        nonlocal calls
        calls += 1
        output.write_bytes(expected.encode())
        if calls == 2:
            raise OSError("download failed")

    monkeypatch.setattr("protoloom.bench.upstream.download", fail_second_download)
    source = {
        "files": [
            {
                "path": f"proto/{name}.proto",
                "url": f"https://example.test/{name}",
                "sha256": character * 64,
                "size": 1,
            }
            for name, character in (("one", "a"), ("two", "b"))
        ]
    }
    root = tmp_path / "source"
    with pytest.raises(OSError, match="download failed"):
        materialize_source(source, tmp_path / "cache", root)
    assert not root.exists()
    assert not tuple(tmp_path.glob(".source.*"))


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
