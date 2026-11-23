"""Validate a preinstalled runtime against the lock for its Python version."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys


def main():
    python_version = sys.version_info[:2]
    missed = []
    for line in Path(__file__).with_name("requirements.txt").read_text().splitlines():
        requirement, _, condition = line.partition(";")
        name, separator, expected = requirement.strip().partition("==")
        if not separator:
            continue
        if '< "3.12"' in condition and python_version >= (3, 12):
            continue
        if '>= "3.12"' in condition and python_version < (3, 12):
            continue
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = "missing"
        if installed != expected:
            missed.append(f"{name}: expected {expected}, found {installed}")
    if missed:
        print("\n".join(missed), file=sys.stderr)
    return bool(missed)


if __name__ == "__main__":
    sys.exit(main())
