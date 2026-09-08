import re
import sys
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?")


def set_version(root: Path, version: str) -> None:
    if VERSION.fullmatch(version) is None:
        raise ValueError(f"invalid release version: {version}")
    replacements = (
        (root / "pyproject.toml", r'(?m)^version = "[^"]+"$', f'version = "{version}"'),
        (
            root / "src/protoloom/__init__.py",
            r'(?m)^__version__ = "[^"]+"$',
            f'__version__ = "{version}"',
        ),
    )
    for path, pattern, replacement in replacements:
        source = path.read_text(encoding="utf-8")
        updated, count = re.subn(pattern, replacement, source)
        if count != 1:
            raise ValueError(f"expected one version declaration in {path}")
        path.write_text(updated, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: set_version.py VERSION")
    set_version(Path(__file__).parents[1], sys.argv[1])


if __name__ == "__main__":
    main()
