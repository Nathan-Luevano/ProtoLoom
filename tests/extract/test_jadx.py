import stat
from pathlib import Path

import pytest

from protoloom.extract.jadx import JadxError, decompile_with_jadx


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
    tool = _executable(tmp_path / "jadx", 'echo "broken input" >&2\nexit 7\n')
    with pytest.raises(JadxError, match="status 7: broken input"):
        decompile_with_jadx(tmp_path / "x.apk", tmp_path / "out", executable=str(tool))


def test_jadx_kills_timed_out_process(tmp_path: Path) -> None:
    tool = _executable(tmp_path / "jadx", "sleep 5\n")
    with pytest.raises(JadxError, match="exceeded"):
        decompile_with_jadx(
            tmp_path / "x.apk",
            tmp_path / "out",
            executable=str(tool),
            timeout_seconds=0.05,
        )


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
