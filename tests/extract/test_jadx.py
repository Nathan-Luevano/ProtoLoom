import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from protoloom.extract.jadx import (
    JadxError,
    _index_candidates,
    _publish_directory,
    decompile_with_jadx,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_jadx_runs_without_a_shell_and_counts_sources(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out/sources/p"\n'
        'printf "return newMessageInfo(x);" > "$out/sources/p/A.java"\n',
    )
    input_path = tmp_path / "sample;touch-not-run.apk"
    input_path.write_bytes(b"PK")
    result = decompile_with_jadx(
        input_path, tmp_path / "result", executable=str(tool), timeout_seconds=2
    )
    assert result.source_files == 1
    assert result.candidate_sites == 1
    assert (result.output / "protoloom-candidates.json").is_file()
    assert not (tmp_path / "touch-not-run.apk").exists()


def test_jadx_reports_nonzero_exit(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'printf "partial" > "$out/partial.java"\n'
        'echo "broken input" >&2\nexit 7\n',
    )
    output = tmp_path / "out"
    with pytest.raises(JadxError, match="status 7: broken input"):
        decompile_with_jadx(tmp_path / "x.apk", output, executable=str(tool))
    assert not output.exists()
    assert not tuple(tmp_path.glob(".out.*"))


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_jadx_rejects_unsafe_output_path(tmp_path: Path, kind: str) -> None:
    output = tmp_path / "out"
    if kind == "symlink":
        victim = tmp_path / "victim"
        victim.mkdir()
        output.symlink_to(victim, target_is_directory=True)
    else:
        output.write_text("occupied", encoding="utf-8")

    with pytest.raises(JadxError, match=r"symlink|not a directory"):
        decompile_with_jadx(tmp_path / "x.apk", output, executable="jadx")

    if kind == "symlink":
        assert list(victim.iterdir()) == []
    else:
        assert output.read_text(encoding="utf-8") == "occupied"


def test_jadx_rejects_non_positive_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="jadx timeout must be positive"):
        decompile_with_jadx(
            tmp_path / "x.apk", tmp_path / "out", executable="jadx", timeout_seconds=0
        )


def test_jadx_reports_missing_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("protoloom.extract.jadx.shutil.which", lambda name: None)
    with pytest.raises(JadxError, match="jadx is not installed"):
        decompile_with_jadx(tmp_path / "x.apk", tmp_path / "out")


def test_jadx_kills_timed_out_process(tmp_path: Path) -> None:
    tool = _executable(tmp_path / "jadx", "sleep 5\n")
    with pytest.raises(JadxError, match="exceeded"):
        decompile_with_jadx(
            tmp_path / "x.apk",
            tmp_path / "out",
            executable=str(tool),
            timeout_seconds=0.05,
        )


def test_jadx_kills_interrupted_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float | None] = []
    kills: list[tuple[int, int]] = []

    class Process:
        pid = 42
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            if len(waits) == 1:
                raise KeyboardInterrupt
            return 0

    def start(*args: object, **kwargs: object) -> Process:
        return Process()

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(
        "protoloom.extract.jadx.os.killpg",
        lambda pid, sig: kills.append((pid, sig)),
    )

    with pytest.raises(KeyboardInterrupt):
        decompile_with_jadx(tmp_path / "x.apk", tmp_path / "out", executable="jadx")

    assert waits == [120.0, None]
    assert kills == [(42, 9)]


def test_jadx_rejects_too_many_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out"\n'
        'touch "$out/A.java" "$out/B.java"\n',
    )
    monkeypatch.setattr("protoloom.extract.jadx.MAX_JADX_SOURCES", 1)

    with pytest.raises(JadxError, match="more than 1 Java sources"):
        decompile_with_jadx(tmp_path / "x.apk", tmp_path / "out", executable=str(tool))


def test_jadx_rejects_oversized_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out"\n'
        'printf "large" > "$out/A.java"\n',
    )
    monkeypatch.setattr("protoloom.extract.jadx.MAX_JADX_SOURCE_SIZE", 4)

    with pytest.raises(JadxError, match="source exceeds 4 bytes"):
        decompile_with_jadx(tmp_path / "x.apk", tmp_path / "out", executable=str(tool))


def test_jadx_rejects_source_replaced_during_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    source = output / "A.java"
    source.write_text("class A {}", encoding="utf-8")
    real_open = Path.open

    def replace_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == source:
            return real_open(Path(os.devnull), "rb")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_open)

    with pytest.raises(JadxError, match="non-file Java source"):
        _index_candidates(output)


def test_jadx_rejects_symlinked_source(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    victim = output / "A.java"
    victim.write_text("class A {}", encoding="utf-8")
    link = output / "B.java"
    link.symlink_to(victim)

    with pytest.raises(JadxError, match="symlinked Java source"):
        _index_candidates(output)


def test_jadx_enforces_size_bound_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    source = output / "A.java"
    source.write_text("x" * 10, encoding="utf-8")
    real_fstat = os.fstat

    class FakeStat:
        def __init__(self, real: os.stat_result) -> None:
            self._real = real
            self.st_size = 5

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(
        "protoloom.extract.jadx.os.fstat", lambda fd: FakeStat(real_fstat(fd))
    )
    monkeypatch.setattr("protoloom.extract.jadx.MAX_JADX_SOURCE_SIZE", 5)

    with pytest.raises(JadxError, match="source exceeds 5 bytes"):
        _index_candidates(output)


def test_jadx_rejects_oversized_total_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "A.java").write_text("aaaa", encoding="utf-8")
    (output / "B.java").write_text("bbbb", encoding="utf-8")
    monkeypatch.setattr("protoloom.extract.jadx.MAX_JADX_SOURCE_TOTAL", 5)

    with pytest.raises(JadxError, match="sources exceed 5 bytes"):
        _index_candidates(output)


def test_jadx_skips_source_deleted_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    source = output / "A.java"
    source.write_text("newMessageInfo(x);", encoding="utf-8")
    real_open = Path.open

    def missing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == source:
            raise OSError("vanished")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", missing_open)

    sources, candidates = _index_candidates(output)
    assert sources == 1
    assert candidates == 0


def test_jadx_rejects_too_many_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "A.java").write_text(
        "newMessageInfo(a);\nnewMessageInfo(b);\n", encoding="utf-8"
    )
    monkeypatch.setattr("protoloom.extract.jadx.MAX_JADX_CANDIDATES", 1)

    with pytest.raises(JadxError, match="more than 1 candidates"):
        _index_candidates(output)


def test_jadx_write_candidates_cleans_up_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "A.java").write_text("newMessageInfo(x);", encoding="utf-8")

    def fail_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("protoloom.extract.jadx.os.fsync", fail_fsync)

    with pytest.raises(OSError, match="disk full"):
        _index_candidates(output)
    assert not tuple(output.glob(".protoloom-candidates.*"))


def test_jadx_candidate_index_replaces_symlink(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out"\n'
        'printf "newMessageInfo(x);" > "$out/A.java"\n',
    )
    output = tmp_path / "out"
    output.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("preserve", encoding="utf-8")
    index = output / "protoloom-candidates.json"
    index.symlink_to(victim)

    result = decompile_with_jadx(tmp_path / "x.apk", output, executable=str(tool))

    assert result.candidate_sites == 1
    assert victim.read_text(encoding="utf-8") == "preserve"
    assert not index.is_symlink()


def test_jadx_success_replaces_stale_output(tmp_path: Path) -> None:
    tool = _executable(
        tmp_path / "jadx",
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  [ "$1" = "-d" ] && out="$2" && shift\n'
        "  shift\n"
        "done\n"
        'printf "class New {}" > "$out/New.java"\n',
    )
    output = tmp_path / "out"
    output.mkdir()
    (output / "Stale.java").write_text("stale", encoding="utf-8")

    decompile_with_jadx(tmp_path / "x.apk", output, executable=str(tool))

    assert not (output / "Stale.java").exists()
    assert (output / "New.java").is_file()
    assert not tuple(tmp_path.glob(".out.*"))


def test_jadx_publication_restores_output_after_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing.java").write_text("preserve", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new.java").write_text("new", encoding="utf-8")
    real_replace = Path.replace

    def fail_staging(path: Path, target: Path) -> Path:
        if path == staging:
            raise OSError("rename failed")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staging)

    with pytest.raises(OSError, match="rename failed"):
        _publish_directory(staging, output)

    assert (output / "existing.java").read_text(encoding="utf-8") == "preserve"
    assert staging.is_dir()
    assert not tuple(tmp_path.glob(".out.old.*"))


def test_jadx_publication_rolls_back_after_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing.java").write_text("preserve", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new.java").write_text("new", encoding="utf-8")
    syncs = 0

    def fail_first_sync(path: Path) -> None:
        nonlocal syncs
        syncs += 1
        if syncs == 1:
            raise OSError("sync failed")

    monkeypatch.setattr("protoloom.extract.jadx._sync_directory", fail_first_sync)

    with pytest.raises(OSError, match="sync failed"):
        _publish_directory(staging, output)

    assert (output / "existing.java").read_text(encoding="utf-8") == "preserve"
    assert (staging / "new.java").read_text(encoding="utf-8") == "new"
    assert syncs == 2
    assert not tuple(tmp_path.glob(".out.old.*"))
