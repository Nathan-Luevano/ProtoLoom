import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT_SIZE = 512 * 1024 * 1024


def load_manifest(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("manifest version must be 1")
    apps = raw.get("apps")
    if not isinstance(apps, list) or not apps:
        raise ValueError("manifest apps must be a non-empty list")
    seen = set()
    for app in apps:
        if not isinstance(app, dict) or not ID_PATTERN.fullmatch(app.get("id", "")):
            raise ValueError("each app needs a safe lowercase id")
        if app["id"] in seen:
            raise ValueError(f"duplicate app id: {app['id']}")
        seen.add(app["id"])
        artifact = app.get("artifact")
        if not isinstance(artifact, dict):
            raise ValueError(f"{app['id']}: artifact must be an object")
        url = artifact.get("url", "")
        if urlparse(url).scheme != "https":
            raise ValueError(f"{app['id']}: artifact URL must use HTTPS")
        if not SHA_PATTERN.fullmatch(artifact.get("sha256", "")):
            raise ValueError(f"{app['id']}: invalid SHA-256")
        size = artifact.get("size")
        if not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT_SIZE:
            raise ValueError(f"{app['id']}: invalid artifact size")
    return apps


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_app(app: dict[str, Any], destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{app['id']}.apk"
    expected_hash = app["artifact"]["sha256"]
    expected_size = app["artifact"]["size"]
    if target.is_symlink():
        raise ValueError(f"refusing symlink destination: {target}")
    if target.exists():
        if (
            target.stat().st_size == expected_size
            and sha256_file(target) == expected_hash
        ):
            return target
        raise ValueError(f"existing artifact does not match manifest: {target}")

    request = urllib.request.Request(
        app["artifact"]["url"], headers={"User-Agent": "PROTOLOOM-corpus-fetch/1"}
    )
    temporary_name = ""
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if urlparse(response.url).scheme != "https":
                raise ValueError("download redirected away from HTTPS")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{app['id']}.",
                suffix=".part",
                dir=destination,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                digest = hashlib.sha256()
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected_size:
                        raise ValueError(
                            f"download exceeds pinned size for {app['id']}"
                        )
                    digest.update(chunk)
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
        if total != expected_size:
            raise ValueError(
                f"download size mismatch for {app['id']}: {total} != {expected_size}"
            )
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"SHA-256 mismatch for {app['id']}")
        os.replace(temporary_name, target)
        temporary_name = ""
        return target
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/corpus/tier-b-real-apps.json"),
    )
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--app", action="append", default=[])
    args = parser.parse_args()
    apps = load_manifest(args.manifest)
    selected = set(args.app)
    known = {app["id"] for app in apps}
    if selected - known:
        parser.error(f"unknown app ids: {', '.join(sorted(selected - known))}")
    for app in apps:
        if selected and app["id"] not in selected:
            continue
        path = fetch_app(app, args.destination)
        print(f"verified {app['id']}: {path}")


if __name__ == "__main__":
    main()
