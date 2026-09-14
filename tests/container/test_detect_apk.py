import importlib
import os
import struct
from pathlib import Path
from typing import IO
from zipfile import ZipFile, ZipInfo

import pytest
from pytest import MonkeyPatch

from protoloom.container.apk import (
    AndroidArchive,
    ArchiveEntry,
    ArchiveError,
    ArchiveInventory,
)
from protoloom.container.detect import ContainerKind, detect, detect_bytes

detect_module = importlib.import_module("protoloom.container.detect")
apk_module = importlib.import_module("protoloom.container.apk")


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (b"dex\n039\x00", ContainerKind.DEX),
        (b"\x7fELF", ContainerKind.ELF),
        (b"\xcf\xfa\xed\xfe", ContainerKind.MACHO),
        (b"MZ", ContainerKind.UNKNOWN),
        (b"nothing", ContainerKind.UNKNOWN),
    ],
)
def test_magic_detection(payload: bytes, kind: ContainerKind) -> None:
    assert detect_bytes(payload).kind is kind


def test_detects_pe_signature_beyond_initial_probe(tmp_path: Path) -> None:
    path = tmp_path / "large-stub.exe"
    payload = bytearray(5004)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 5000)
    payload[5000:] = b"PE\x00\x00"
    path.write_bytes(payload)
    assert detect(path).kind is ContainerKind.PE


def test_detect_pe_rejects_file_shorter_than_header(tmp_path: Path) -> None:
    path = tmp_path / "tiny.exe"
    path.write_bytes(b"MZ" + b"\x00" * 8)
    assert detect(path).kind is ContainerKind.UNKNOWN


def test_detect_pe_rejects_mismatched_signature_beyond_probe(tmp_path: Path) -> None:
    path = tmp_path / "mismatch.exe"
    payload = bytearray(5004)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 5000)
    payload[5000:] = b"XX\x00\x00"
    path.write_bytes(payload)
    assert detect(path).kind is ContainerKind.UNKNOWN


def test_rejects_truncated_pe_signature_offset(tmp_path: Path) -> None:
    path = tmp_path / "truncated.exe"
    payload = bytearray(64)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 64)
    path.write_bytes(payload)
    assert detect(path).kind is ContainerKind.UNKNOWN


def test_detects_pe_signature_within_initial_probe(tmp_path: Path) -> None:
    path = tmp_path / "small-stub.exe"
    payload = bytearray(200)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 100)
    payload[100:104] = b"PE\x00\x00"
    path.write_bytes(payload)
    assert detect(path).kind is ContainerKind.PE


def test_detect_treats_corrupt_zip_magic_as_unknown(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.zip"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    assert detect(path).kind is ContainerKind.UNKNOWN


def test_detect_finds_zip_behind_a_prepended_stub(tmp_path: Path) -> None:
    # Self-extracting archives and "reverse signed" polyglots prepend bytes
    # before the local file headers; the ZIP's real directory lives in the
    # trailing EOCD record, which ZipFile finds regardless of what precedes it.
    path = tmp_path / "sfx.bin"
    with ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"dex\n039\x00" + b"\x00" * 20)
    stub = b"NOT A ZIP MAGIC AT ALL" * 4
    path.write_bytes(stub + path.read_bytes())
    assert detect(path).kind is ContainerKind.APK


def test_detect_front_magic_wins_over_appended_zip(tmp_path: Path) -> None:
    # An ELF (or DEX/Mach-O) binary with a ZIP appended at the end must stay
    # classified by its real front format, not get reclassified as the zip.
    path = tmp_path / "elf_with_zip_tail.bin"
    zip_path = tmp_path / "tail.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"dex\n039\x00" + b"\x00" * 20)
    path.write_bytes(b"\x7fELF" + b"\x00" * 32 + zip_path.read_bytes())
    assert detect(path).kind is ContainerKind.ELF


def test_classify_zip_detects_bundle_and_jar_and_plain_zip(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.zip"
    with ZipFile(bundle, "w") as archive:
        archive.writestr("BundleConfig.pb", b"cfg")
    assert detect(bundle).kind is ContainerKind.AAB

    jar = tmp_path / "jar.zip"
    with ZipFile(jar, "w") as archive:
        archive.writestr("com/example/Main.class", b"class")
    assert detect(jar).kind is ContainerKind.JAR

    plain = tmp_path / "plain.zip"
    with ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", b"hi")
    assert detect(plain).kind is ContainerKind.ZIP


def test_bundle_shaped_aab_finds_dex_across_every_split_module(tmp_path: Path) -> None:
    # A real bundletool .aab has one dex/manifest pair per module (base,
    # feature1, an install-time feature, ...) rather than one flat set of
    # classes*.dex at the archive root like an APK. Detection and dex
    # selection must not assume the APK's flat layout.
    aab = tmp_path / "real_shaped.aab"
    with ZipFile(aab, "w") as archive:
        archive.writestr("BundleConfig.pb", b"cfg")
        archive.writestr("base/manifest/AndroidManifest.xml", b"stub")
        archive.writestr("base/dex/classes.dex", b"base-dex-1")
        archive.writestr("base/dex/classes2.dex", b"base-dex-2")
        archive.writestr("feature1/manifest/AndroidManifest.xml", b"stub")
        archive.writestr("feature1/dex/classes.dex", b"feature1-dex")
        archive.writestr("install_time_feature/manifest/AndroidManifest.xml", b"stub")

    assert detect(aab).kind is ContainerKind.AAB
    inventory = AndroidArchive(aab).inventory()
    dex_names = {entry.name for entry in inventory.dex_files}
    assert dex_names == {
        "base/dex/classes.dex",
        "base/dex/classes2.dex",
        "feature1/dex/classes.dex",
    }


def test_detection_rejects_special_and_oversized_inputs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    with pytest.raises(OSError, match="input is not a regular file"):
        detect(Path(os.devnull))
    path = tmp_path / "large"
    path.write_bytes(b"large")
    monkeypatch.setattr(detect_module, "MAX_CONTAINER_SIZE", 4)
    with pytest.raises(OSError, match="input exceeds 4 bytes"):
        detect(path)


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


def test_archive_operations_reject_special_files() -> None:
    source = AndroidArchive(Path(os.devnull))

    with pytest.raises(ArchiveError, match="invalid archive"):
        source.inventory()
    with pytest.raises(ArchiveError, match="cannot read archive member"):
        source.read("classes.dex")
    with pytest.raises(ArchiveError, match="cannot read archive"):
        list(source.iter_read((ArchiveEntry("classes.dex", 0, 0, "dex"),)))


def test_archive_inventory_uses_valid_multidex_names(tmp_path: Path) -> None:
    path = tmp_path / "multidex.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes2.dex", b"secondary")
        archive.writestr("classesbackup.dex", b"resource")
    inventory = AndroidArchive(path).inventory()
    assert [entry.name for entry in inventory.dex_files] == ["classes2.dex"]


def test_zip_detection_does_not_copy_archive_names(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"")
        archive.writestr("classes.dex", b"")

    class BoundedZipFile(ZipFile):
        def namelist(self) -> list[str]:
            raise AssertionError("namelist called")

    monkeypatch.setattr(detect_module, "ZipFile", BoundedZipFile)

    assert detect(path).kind is ContainerKind.APK


def test_zip_detection_bounds_metadata(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"")
        archive.writestr("classes.dex", b"")
    monkeypatch.setattr(detect_module, "MAX_ZIP_DETECTION_ENTRIES", 1)

    result = detect(path)

    assert result.kind is ContainerKind.ZIP
    assert result.detail == "archive metadata limit exceeded"


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
    with pytest.raises(ValueError, match="must be positive"):
        inventory.select({"dex"}, max_total_size=0)


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


def test_archive_inventory_bounds_entry_count(tmp_path: Path) -> None:
    path = tmp_path / "many.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("one", b"")
        archive.writestr("two", b"")

    with pytest.raises(ArchiveError, match="more than 1 entries"):
        AndroidArchive(path).inventory(max_entries=1)


def test_archive_inventory_bounds_name_bytes(tmp_path: Path) -> None:
    path = tmp_path / "names.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("long-name", b"")

    with pytest.raises(ArchiveError, match="names exceed 8 bytes"):
        AndroidArchive(path).inventory(max_name_bytes=8)


def test_archive_inventory_rejects_nonpositive_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        AndroidArchive(tmp_path / "missing.zip").inventory(max_entries=0)


def test_archive_reads_reject_nonpositive_limits(tmp_path: Path) -> None:
    source = AndroidArchive(tmp_path / "missing.zip")
    with pytest.raises(ValueError, match="must be positive"):
        source.read("classes.dex", max_size=0)
    with pytest.raises(ValueError, match="must be positive"):
        list(source.iter_read((), max_size=0))


def test_cached_archive_read_validates_member_name(tmp_path: Path) -> None:
    source = AndroidArchive(tmp_path / "unused.zip")
    entry = ArchiveEntry("../classes.dex", 3, 3, "dex")
    with pytest.raises(ArchiveError, match="unsafe"):
        list(source.iter_read((entry,), cached={entry.name: b"dex"}))


@pytest.mark.parametrize("error", [RuntimeError("encrypted"), NotImplementedError()])
def test_archive_read_normalizes_zip_runtime_errors(
    tmp_path: Path, monkeypatch: MonkeyPatch, error: Exception
) -> None:
    path = tmp_path / "sample.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex")

    def fail_read(*args: object) -> bytes:
        raise error

    monkeypatch.setattr(apk_module, "_read_member", fail_read)
    with pytest.raises(ArchiveError, match="cannot read archive member"):
        AndroidArchive(path).read("classes.dex")


@pytest.mark.parametrize("name", ["../classes.dex", "/classes.dex", "..\\classes.dex"])
def test_archive_inventory_rejects_unsafe_members(tmp_path: Path, name: str) -> None:
    path = tmp_path / "unsafe.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr(name, b"dex")

    with pytest.raises(ArchiveError, match="unsafe archive member"):
        AndroidArchive(path).inventory()


def test_archive_inventory_skips_directory_entries_and_detects_class_kind(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("assets/", b"")
        archive.writestr("Sample.class", b"class")

    inventory = AndroidArchive(path).inventory()

    assert {(entry.name, entry.kind) for entry in inventory.entries} == {
        ("Sample.class", "class")
    }


def test_archive_read_reports_missing_member(tmp_path: Path) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex")

    with pytest.raises(ArchiveError, match="archive member not found"):
        AndroidArchive(path).read("missing.dex")


def test_archive_read_rejects_member_larger_than_declared_after_reading(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex")

    real_open = ZipFile.open

    class _OverReadingStream:
        def __init__(self, real: IO[bytes]) -> None:
            self._real = real

        def read(self, size: int) -> bytes:
            return b"x" * (size + 1)

        def __enter__(self) -> "_OverReadingStream":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            self._real.__exit__(exc_type, exc, traceback)  # type: ignore[arg-type]

    def fake_open(self: ZipFile, info: str | ZipInfo) -> object:
        return _OverReadingStream(real_open(self, info))

    monkeypatch.setattr(ZipFile, "open", fake_open)
    with pytest.raises(ArchiveError, match="exceeds"):
        AndroidArchive(path).read("classes.dex", max_size=3)


def test_iter_dex_selects_only_dex_entries(tmp_path: Path) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex\n039\x00")
        archive.writestr("classes2.dex", b"dex\n039\x00")
        archive.writestr("assets/schema.pb", b"proto")

    names = {entry.name for entry, _ in AndroidArchive(path).iter_dex()}

    assert names == {"classes.dex", "classes2.dex"}


def test_iter_read_never_opens_archive_when_all_entries_cached(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    entry = ArchiveEntry("classes.dex", 4, 4, "dex")

    def fail_open(*args: object, **kwargs: object) -> ZipFile:
        raise AssertionError("should not open the archive")

    monkeypatch.setattr("protoloom.container.apk.open_limited", fail_open)
    source = AndroidArchive(tmp_path / "unused.zip")

    values = list(source.iter_read((entry,), cached={entry.name: b"dex\n"}))

    assert values == [(entry, b"dex\n")]


def test_module_level_inventory_delegates_to_android_archive(tmp_path: Path) -> None:
    path = tmp_path / "sample.apk"
    with ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", b"dex")

    assert (
        apk_module.inventory(path).entries == AndroidArchive(path).inventory().entries
    )
