import argparse
from pathlib import Path

from protoloom.container.apk import AndroidArchive
from protoloom.container.dex import DexFile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    args = parser.parse_args()
    try:
        from androguard.core.dex import DEX  # type: ignore[import-not-found]
    except ImportError as error:
        raise SystemExit("install androguard to run the DEX oracle") from error

    checked = 0
    for entry, data in AndroidArchive(args.apk).iter_dex():
        recovered = DexFile(data).strings
        oracle = tuple(DEX(data).get_strings())
        if recovered != oracle:
            mismatch = next(
                (
                    index
                    for index, values in enumerate(zip(recovered, oracle, strict=False))
                    if values[0] != values[1]
                ),
                min(len(recovered), len(oracle)),
            )
            raise SystemExit(f"{entry.name}: string pool differs at index {mismatch}")
        print(f"{entry.name}: {len(recovered)} strings match")
        checked += 1
    if not checked:
        raise SystemExit("APK contains no DEX files")


if __name__ == "__main__":
    main()
