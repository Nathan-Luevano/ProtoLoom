from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    available: bool
    required: bool
    location: str | None
    purpose: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    dependencies: tuple[DependencyStatus, ...]

    @property
    def healthy(self) -> bool:
        return all(item.available for item in self.dependencies if item.required)

    @property
    def missing_optional(self) -> tuple[DependencyStatus, ...]:
        return tuple(
            item
            for item in self.dependencies
            if not item.required and not item.available
        )


def diagnose(
    *,
    which: Callable[[str], str | None] = shutil.which,
    module_available: Callable[[str], bool] | None = None,
) -> DoctorReport:
    lookup = module_available or _module_available
    dependencies = (
        _executable("protoc", True, "compile recovered schemas", which),
        _module("google.protobuf", True, "descriptor serialization", lookup),
        _executable("jadx", False, "opt-in Android decompiler fallback", which),
        _executable("java", False, "run the jadx fallback", which),
        _module("lief", False, "extended native binary parsing", lookup),
        _module("androguard", False, "DEX differential test oracle", lookup),
    )
    return DoctorReport(dependencies)


def format_report(report: DoctorReport) -> str:
    lines = ["PROTOLOOM dependency check"]
    for item in report.dependencies:
        state = "ok" if item.available else "missing"
        requirement = "required" if item.required else "optional"
        location = f" ({item.location})" if item.location else ""
        lines.append(f"[{state}] {item.name} — {requirement}{location}: {item.purpose}")
    lines.append("Ready." if report.healthy else "Missing required dependencies.")
    return "\n".join(lines) + "\n"


def _executable(
    name: str,
    required: bool,
    purpose: str,
    which: Callable[[str], str | None],
) -> DependencyStatus:
    location = which(name)
    return DependencyStatus(name, location is not None, required, location, purpose)


def _module(
    name: str,
    required: bool,
    purpose: str,
    available: Callable[[str], bool],
) -> DependencyStatus:
    found = available(name)
    return DependencyStatus(name, found, required, "python" if found else None, purpose)


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
