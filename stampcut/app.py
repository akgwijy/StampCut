import sys

from stampcut import __version__


def main(argv: list[str] | None = None) -> int:
    print(f"StampCut {__version__}")
    return 0
