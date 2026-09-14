import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from protoloom.tui.jobs import ExtractionJob, ExtractionRequest


def test_builds_existing_cli_command() -> None:
    request = ExtractionRequest(
        Path("input app.apk"),
        Path("output dir"),
        allow_heuristic_lite=True,
        jadx=True,
    )

    assert request.command() == (
        sys.executable,
        "-m",
        "protoloom.cli",
        "extract",
        "input app.apk",
        "--output",
        "output dir",
        "--allow-heuristic-lite",
        "--jadx",
    )


def test_streams_cli_failure_without_shell(tmp_path: Path) -> None:
    lines: list[str] = []
    job = ExtractionJob()

    result = asyncio.run(
        job.run(ExtractionRequest(tmp_path / "missing.apk", tmp_path), lines.append)
    )

    assert result.returncode == 2
    assert result.cancelled is False
    assert any("file does not exist" in line for line in lines)
    assert job.running is False
    assert job._process is None


def test_rejects_concurrent_run_during_process_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> tuple[bool, bool]:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_create(
            *args: object, **kwargs: object
        ) -> asyncio.subprocess.Process:
            entered.set()
            await release.wait()
            raise AssertionError("startup should remain paused")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create)
        job = ExtractionJob()
        request = ExtractionRequest(tmp_path / "missing.apk", tmp_path)
        task = asyncio.create_task(job.run(request, lambda line: None))
        await entered.wait()
        active_during_startup = job.running
        with pytest.raises(RuntimeError, match="already running"):
            await job.run(request, lambda line: None)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return active_during_startup, job.running

    active_during_startup, active_after_cancel = asyncio.run(exercise())
    assert active_during_startup is True
    assert active_after_cancel is False


def test_cancels_job_requested_during_process_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )
    real_create = asyncio.create_subprocess_exec

    async def exercise() -> tuple[bool, bool]:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_create(
            *args: str, **kwargs: Any
        ) -> asyncio.subprocess.Process:
            entered.set()
            await release.wait()
            return await real_create(*args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create)
        job = ExtractionJob()
        task = asyncio.create_task(
            job.run(ExtractionRequest(tmp_path, tmp_path), lambda line: None)
        )
        await entered.wait()
        await job.cancel()
        release.set()
        result = await task
        return result.cancelled, job.running

    cancelled, running = asyncio.run(exercise())
    assert cancelled is True
    assert running is False


def test_stops_process_with_oversized_output_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; print('x' * 100000, flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )
    lines: list[str] = []
    job = ExtractionJob()

    result = asyncio.run(job.run(ExtractionRequest(tmp_path, tmp_path), lines.append))

    assert result.returncode != 0
    assert result.cancelled is False
    assert lines == ["Output line exceeded 4096 bytes"]
    assert job.running is False
    assert job._process is None


def test_stops_process_after_output_line_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = (
        "import time; print('one'); print('two'); "
        "print('three', flush=True); time.sleep(30)"
    )
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )
    monkeypatch.setattr("protoloom.tui.jobs.MAX_JOB_OUTPUT_LINES", 2)
    lines: list[str] = []
    job = ExtractionJob()

    result = asyncio.run(job.run(ExtractionRequest(tmp_path, tmp_path), lines.append))

    assert result.returncode != 0
    assert result.cancelled is False
    assert lines == ["one", "two", "Process output exceeded 2 lines"]
    assert job.running is False
    assert job._process is None


@pytest.mark.parametrize("error", [ValueError("callback"), RuntimeError("callback")])
def test_stops_process_when_output_callback_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> bool:
        job = ExtractionJob()

        def fail(line: str) -> None:
            raise error

        with pytest.raises(type(error), match="callback"):
            await job.run(ExtractionRequest(tmp_path, tmp_path), fail)
        return job.running

    assert asyncio.run(exercise()) is False


def test_stop_and_drain_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> set[str]:
        job = ExtractionJob()
        started: set[str] = set()
        both_started = asyncio.Event()

        async def wait_for_other(name: str) -> None:
            started.add(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        monkeypatch.setattr(job, "_stop", lambda: wait_for_other("stop"))
        monkeypatch.setattr(job, "_drain_output", lambda: wait_for_other("drain"))
        await job._stop_and_drain()
        assert both_started.is_set()
        return started

    assert asyncio.run(exercise()) == {"stop", "drain"}


def test_cancels_long_running_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> tuple[bool, list[str]]:
        job = ExtractionJob()
        lines: list[str] = []
        task = asyncio.create_task(
            job.run(ExtractionRequest(tmp_path, tmp_path), lines.append)
        )
        while not lines:
            await asyncio.sleep(0.01)
        await job.cancel()
        return (await task).cancelled, lines

    cancelled, lines = asyncio.run(exercise())

    assert cancelled is True
    assert lines == ["ready"]


def test_cancel_is_a_noop_when_not_running() -> None:
    job = ExtractionJob()
    asyncio.run(job.cancel())
    assert job.running is False


def test_stop_is_a_noop_without_a_process() -> None:
    job = ExtractionJob()
    asyncio.run(job._stop())


def test_drain_output_is_a_noop_without_a_process() -> None:
    job = ExtractionJob()
    asyncio.run(job._drain_output())


def test_stop_escalates_to_sigkill_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )
    signals: list[signal.Signals] = []

    async def exercise() -> list[signal.Signals]:
        job = ExtractionJob()
        lines: list[str] = []
        task = asyncio.create_task(
            job.run(ExtractionRequest(tmp_path, tmp_path), lines.append)
        )
        while not lines:
            await asyncio.sleep(0.01)

        real_signal = ExtractionJob._signal

        def record_signal(
            process: asyncio.subprocess.Process, value: signal.Signals
        ) -> None:
            signals.append(value)
            real_signal(process, value)

        monkeypatch.setattr(ExtractionJob, "_signal", staticmethod(record_signal))

        async def immediate_timeout(
            awaitable: Any, timeout: float | None = None
        ) -> None:
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise TimeoutError

        monkeypatch.setattr("protoloom.tui.jobs.asyncio.wait_for", immediate_timeout)
        await job.cancel()
        await task
        return signals

    observed = asyncio.run(exercise())
    assert observed == [signal.SIGTERM, signal.SIGKILL]


def test_signal_uses_process_group_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr("protoloom.tui.jobs.os.name", "posix")
    monkeypatch.setattr(
        "protoloom.tui.jobs.os.killpg", lambda pid, value: calls.append((pid, value))
    )

    class FakeProcess:
        pid = 99

    ExtractionJob._signal(FakeProcess(), signal.SIGTERM)  # type: ignore[arg-type]

    assert calls == [(99, signal.SIGTERM)]


def test_signal_suppresses_missing_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.tui.jobs.os.name", "posix")

    def raise_lookup(pid: int, value: object) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("protoloom.tui.jobs.os.killpg", raise_lookup)

    class FakeProcess:
        pid = 99

    ExtractionJob._signal(FakeProcess(), signal.SIGTERM)  # type: ignore[arg-type]


def test_signal_uses_process_methods_off_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.tui.jobs.os.name", "nt")
    calls: list[str] = []

    class FakeProcess:
        pid = 99

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    process = FakeProcess()
    ExtractionJob._signal(process, signal.SIGTERM)  # type: ignore[arg-type]
    ExtractionJob._signal(process, signal.SIGKILL)  # type: ignore[arg-type]

    assert calls == ["terminate", "kill"]


def test_task_cancellation_stops_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> bool:
        job = ExtractionJob()
        lines: list[str] = []
        task = asyncio.create_task(
            job.run(ExtractionRequest(tmp_path, tmp_path), lines.append)
        )
        while not lines:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return job.running

    assert asyncio.run(exercise()) is False
