from pathlib import Path
from zipfile import ZipFile

import pytest

from protoloom.container.apk import AndroidArchive, ArchiveError
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
