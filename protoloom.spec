# ruff: noqa: F821

from PyInstaller.utils.hooks import collect_submodules

hidden_imports = collect_submodules("google.protobuf")

analysis = Analysis(
    ["src/protoloom/cli.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["androguard", "lief"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="protoloom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
