"""Fail a release build when the frozen manager is missing required Tk modules."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path


REQUIRED_MODULES = frozenset(
    {
        "tkinter.messagebox",
        "tkinter.simpledialog",
        "tkinter.ttk",
    }
)


def normalise_module_name(entry_name: str) -> str:
    """Convert PyInstaller archive paths/module entries to dotted module names."""
    module_name = entry_name.replace("\\", "/")
    for suffix in (".pyc", ".pyo", ".py"):
        if module_name.endswith(suffix):
            module_name = module_name[: -len(suffix)]
            break
    if module_name.endswith("/__init__"):
        module_name = module_name[: -len("/__init__")]
    return module_name.replace("/", ".")


def missing_required_modules(entry_names: Iterable[str]) -> set[str]:
    bundled_modules = {normalise_module_name(name) for name in entry_names}
    return set(REQUIRED_MODULES - bundled_modules)


def read_archive_entries(executable: Path) -> set[str]:
    """Read top-level and embedded PYZ entries from a PyInstaller executable."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:  # pragma: no cover - exercised by release environment
        raise RuntimeError("PyInstaller is required to inspect the manager bundle") from exc

    archive = CArchiveReader(str(executable))
    entries = set(archive.toc)
    for name, (*_, typecode) in archive.toc.items():
        if typecode != "z":
            continue
        embedded = archive.open_embedded_archive(name)
        entries.update(embedded.toc)
    return entries


def verify_bundle(executable: Path) -> None:
    if not executable.is_file():
        raise FileNotFoundError(f"Manager executable does not exist: {executable}")

    missing = missing_required_modules(read_archive_entries(executable))
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(f"Manager bundle is missing required Tk modules: {names}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    verify_bundle(args.executable)
    print("Manager bundle contains required Tk modules: " + ", ".join(sorted(REQUIRED_MODULES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
