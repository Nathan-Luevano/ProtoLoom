from pathlib import Path
from zipfile import ZipFile

import pytest
from pytest import MonkeyPatch

from protoloom.container.apk import (
    AndroidArchive,
    ArchiveEntry,
    ArchiveError,
    ArchiveInventory,
)
from protoloom.container.detect import ContainerKind, detect, detect_bytes


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (b"dex\n039\x00", ContainerKind.DEX),
        (b"\x7fELF", ContainerKind.ELF),
        (b"\xcf\xfa\xed\xfe", ContainerKind.MACHO),
        (b"MZ", ContainerKind.PE),
        (b"nothing", ContainerKind.UNKNOWN),
    ],
)
def test_magic_detection(payload: bytes, kind: ContainerKind) -> None:
    assert detect_bytes(payload).kind is kind


def test_apk_inventory(tmp_path: Path) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"dex\n039\x00")
        archive.writestr("lib/arm64-v8a/libsample.so", b"\x7fELF")
        archive.writestr("assets/schema.pb", b"proto")

    assert detect(path).kind is ContainerKind.APK
    inventory = AndroidArchive(path).inventory()
    assert [entry.name for entry in inventory.dex_files] == ["classes.dex"]
    assert [entry.name for entry in inventory.native_libraries] == [
        "lib/arm64-v8a/libsample.so"
    ]
    assert AndroidArchive(path).read("assets/schema.pb") == b"proto"


def test_archive_read_rejects_unsafe_and_oversized_names(tmp_path: Path) -> None:
    path = tmp_path / "sample.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("large", b"1234")
    source = AndroidArchive(path)
    with pytest.raises(ArchiveError, match="unsafe"):
        source.read("../large")
    with pytest.raises(ArchiveError, match="exceeds"):
        source.read("large", max_size=3)


def test_archive_iterator_opens_once_and_reuses_cached_data(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "sample.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"stored dex")
        archive.writestr("assets/schema.pb", b"stored schema")
    source = AndroidArchive(path)
    entries = source.inventory().entries
    real_zip = ZipFile
    opens = 0

    def open_zip(file: str | Path) -> ZipFile:
        nonlocal opens
        opens += 1
        return real_zip(file)

    monkeypatch.setattr("protoloom.container.apk.ZipFile", open_zip)
    values = list(source.iter_read(entries, cached={"classes.dex": b"cached dex"}))

    assert opens == 1
    assert values == [
        (entries[0], b"cached dex"),
        (entries[1], b"stored schema"),
    ]
    with pytest.raises(ArchiveError, match="exceeds 3 bytes"):
        list(
            source.iter_read(entries[:1], cached={"classes.dex": b"large"}, max_size=3)
        )


def test_archive_inventory_bounds_selected_uncompressed_size() -> None:
    inventory = ArchiveInventory(
        (
            ArchiveEntry("classes.dex", 4, 2, "dex"),
            ArchiveEntry("assets/schema.pb", 3, 1, "asset"),
            ArchiveEntry("res/icon.png", 100, 10, "resource"),
        )
    )

    assert inventory.select({"dex"}, max_total_size=4) == (inventory.entries[0],)
    with pytest.raises(ArchiveError, match="uncompressed bytes"):
        inventory.select({"dex", "asset"}, max_total_size=6)


def test_archive_inventory_rejects_duplicate_members(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.apk"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        ZipFile(path, "w") as archive,
    ):
        archive.writestr("classes.dex", b"first")
        archive.writestr("classes.dex", b"second")

    with pytest.raises(ArchiveError, match="duplicate archive member"):
        AndroidArchive(path).inventory()


@pytest.mark.parametrize("name", ["../classes.dex", "/classes.dex", "..\\classes.dex"])
def test_archive_inventory_rejects_unsafe_members(tmp_path: Path, name: str) -> None:
    path = tmp_path / "unsafe.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr(name, b"dex")

    with pytest.raises(ArchiveError, match="unsafe archive member"):
        AndroidArchive(path).inventory()
