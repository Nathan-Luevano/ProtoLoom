import hashlib
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise ValueError(f"archive URL must be unauthenticated HTTPS: {value}")
    return value


def validate_source_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
        raise ValueError("source manifest needs a sources array")
    names: set[str] = set()
    targets: set[str] = set()
    for source in value["sources"]:
        if not isinstance(source, dict):
            raise ValueError("source entry must be an object")
        name = source.get("name")
        commit = source.get("commit")
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise ValueError(f"unsafe or duplicate source name: {name}")
        names.add(name)
        if not isinstance(commit, str) or len(commit) != 40:
            raise ValueError(f"source {name} needs a full commit SHA")
        files = source.get("files")
        if files is None:
            _validate_remote(source, f"source {name}")
        elif isinstance(files, list) and files:
            for artifact in files:
                if not isinstance(artifact, dict):
                    raise ValueError("source file must be an object")
                path = Path(str(artifact.get("path", "")))
                if path.is_absolute() or ".." in path.parts or not path.name:
                    raise ValueError(f"unsafe source file path: {path}")
                _validate_remote(artifact, f"source file {path}")
        else:
            raise ValueError(f"source {name} files must be a non-empty array")
        includes = source.get("includes")
        if not isinstance(includes, list) or not includes:
            raise ValueError(f"source {name} needs include roots")
        for include in includes:
            path = Path(str(include))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe include root: {include}")
        entries = source.get("targets")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"source {name} needs targets")
        for target in entries:
            if not isinstance(target, dict):
                raise ValueError("target entry must be an object")
            target_name = target.get("name")
            proto = Path(str(target.get("proto", "")))
            if (
                not isinstance(target_name, str)
                or Path(target_name).name != target_name
                or target_name in targets
            ):
                raise ValueError(f"unsafe or duplicate target name: {target_name}")
            if proto.is_absolute() or ".." in proto.parts or proto.suffix != ".proto":
                raise ValueError(f"unsafe target proto: {proto}")
            if target.get("compiled_leg") not in {None, "cpp-object"}:
                raise ValueError(f"unsupported compiled leg: {target['compiled_leg']}")
            targets.add(target_name)
    return value


def _validate_remote(value: dict[str, Any], label: str) -> None:
    digest = value.get("sha256")
    size = value.get("size")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"{label} needs a SHA-256")
    if not isinstance(size, int) or size <= 0:
        raise ValueError(f"{label} needs a positive pinned size")
    https_url(str(value.get("url", "")))


def download(url: str, expected: str, size: int, destination: Path) -> None:
    https_url(url)
    if size <= 0 or size > 128 * 1024 * 1024:
        raise ValueError(f"archive size is outside the 128 MiB limit: {size}")
    if destination.is_symlink():
        raise ValueError(f"archive cache path is a symlink: {destination}")
    if (
        destination.is_file()
        and destination.stat().st_size == size
        and sha256(destination) == expected
    ):
        return
    partial = destination.with_name(destination.name + ".part")
    if partial.is_symlink():
        raise ValueError(f"partial cache path is a symlink: {partial}")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "protoloom-corpus/1"})
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            partial.open("xb") as out,
        ):
            https_url(response.geturl())
            written = 0
            while chunk := response.read(min(1024 * 1024, size + 1 - written)):
                written += len(chunk)
                if written > size:
                    raise ValueError(f"archive exceeds pinned size: {url}")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if written != size:
            raise ValueError(f"archive size mismatch: expected {size}, got {written}")
        actual = sha256(partial)
        if actual != expected:
            raise ValueError(
                f"archive hash mismatch: expected {expected}, got {actual}"
            )
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if not members:
            raise ValueError(f"empty archive: {archive}")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"non-file archive member refused: {member.name}")
        for member in members:
            output = destination / member.name
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read archive member: {member.name}")
            with source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream)
    roots = {Path(member.name).parts[0] for member in members if member.name}
    if len(roots) != 1:
        raise ValueError(f"archive needs one root directory: {archive}")
    return destination / roots.pop()


def materialize_source(source: dict[str, Any], cache: Path, root: Path) -> Path:
    files = source.get("files")
    if files is None:
        archive = cache / f"{source['name']}-{source['commit']}.tar.gz"
        download(source["url"], source["sha256"], source["size"], archive)
        return extract(archive, root)
    root.mkdir(parents=True, exist_ok=True)
    for artifact in files:
        output = root / artifact["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        download(artifact["url"], artifact["sha256"], artifact["size"], output)
    return root
