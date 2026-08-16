import hashlib
import itertools
import json
import shutil
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CorpusError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    sha256: str
    path: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.url is None):
            raise CorpusError(f"artifact {self.name!r} needs exactly one source")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise CorpusError(f"artifact {self.name!r} has an invalid SHA-256")
        if Path(self.name).name != self.name:
            raise CorpusError(f"artifact name is unsafe: {self.name!r}")


@dataclass(frozen=True, slots=True)
class CorpusTarget:
    name: str
    truth: Artifact
    recovered: Artifact


@dataclass(frozen=True, slots=True)
class CompilationJob:
    target: CorpusTarget
    variant: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    name: str
    targets: tuple[CorpusTarget, ...]
    matrix: Mapping[str, tuple[str, ...]]
    root: Path

    def variants(self) -> tuple[Mapping[str, str], ...]:
        keys = tuple(self.matrix)
        products = itertools.product(*(self.matrix[key] for key in keys))
        return tuple(dict(zip(keys, values, strict=True)) for values in products)

    def compilation_jobs(self) -> tuple[CompilationJob, ...]:
        return tuple(
            CompilationJob(target, variant)
            for target in self.targets
            for variant in self.variants()
        )


def drive_compilation_matrix(
    manifest: CorpusManifest, build: Callable[[CompilationJob], None]
) -> None:
    for job in manifest.compilation_jobs():
        build(job)


def load_manifest(path: Path) -> CorpusManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise CorpusError("manifest root must be an object")
        targets = tuple(_target(item) for item in _list(raw, "targets"))
        if not targets:
            raise CorpusError("manifest must contain at least one target")
        names = [target.name for target in targets]
        if len(set(names)) != len(names):
            raise CorpusError("target names must be unique")
        matrix_raw = raw.get("matrix", {})
        if not isinstance(matrix_raw, dict):
            raise CorpusError("matrix must be an object")
        matrix = {
            str(key): tuple(str(value) for value in _as_list(values, "matrix value"))
            for key, values in matrix_raw.items()
        }
        if any(not values for values in matrix.values()):
            raise CorpusError("matrix axes cannot be empty")
        return CorpusManifest(str(raw["name"]), targets, matrix, path.parent.resolve())
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise CorpusError(f"invalid corpus manifest: {path}") from error


def materialize(manifest: CorpusManifest, destination: Path) -> Mapping[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, Path] = {}
    for target in manifest.targets:
        for artifact in (target.truth, target.recovered):
            key = f"{target.name}/{artifact.name}"
            output = destination / target.name / artifact.name
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists() or sha256(output) != artifact.sha256:
                _copy_artifact(manifest.root, artifact, output)
            digest = sha256(output)
            if digest != artifact.sha256:
                output.unlink(missing_ok=True)
                raise CorpusError(
                    f"SHA-256 mismatch for {key}: expected "
                    f"{artifact.sha256}, got {digest}"
                )
            resolved[key] = output
    return resolved


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_artifact(root: Path, artifact: Artifact, output: Path) -> None:
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        if artifact.path is not None:
            source = (root / artifact.path).resolve()
            if not source.is_relative_to(root):
                raise CorpusError(f"artifact path escapes corpus root: {artifact.path}")
            with source.open("rb") as reader, temporary.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
        else:
            assert artifact.url is not None
            with (
                urllib.request.urlopen(artifact.url, timeout=30) as response,
                temporary.open("wb") as writer,
            ):
                shutil.copyfileobj(response, writer)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _target(value: object) -> CorpusTarget:
    if not isinstance(value, dict):
        raise CorpusError("target must be an object")
    return CorpusTarget(
        str(value["name"]),
        _artifact(_mapping(value["truth"], "truth")),
        _artifact(_mapping(value["recovered"], "recovered")),
    )


def _artifact(value: Mapping[str, Any]) -> Artifact:
    return Artifact(
        name=str(value["name"]),
        sha256=str(value["sha256"]),
        path=str(value["path"]) if "path" in value else None,
        url=str(value["url"]) if "url" in value else None,
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CorpusError(f"{label} must be an object")
    return value


def _list(value: Mapping[str, Any], key: str) -> list[object]:
    return _as_list(value[key], key)


def _as_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CorpusError(f"{label} must be an array")
    return value
