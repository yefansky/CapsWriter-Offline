"""Prove that a frozen manager uses only its packaged runtime dependencies."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from typing import Iterable


FROZEN_MODULES = (
    "tkinter",
    "tkinter.messagebox",
    "tkinter.simpledialog",
    "tkinter.ttk",
    "sounddevice",
    "PIL.Image",
    "pynput",
    "pystray",
)
EDITABLE_MODULES = (
    "config_server",
    "core.client.audio.devices",
    "core.runtime_settings",
)


def _resolve(path: str | Path) -> Path:
    return Path(path).resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def forbidden_sys_path_entries(
    entries: Iterable[str], executable_dir: Path, bundle_dir: Path
) -> list[str]:
    """Reject every import path except the app root and this exe's own bundle."""
    executable_dir = executable_dir.resolve()
    bundle_dir = bundle_dir.resolve()
    forbidden: list[str] = []
    for entry in entries:
        resolved = _resolve(entry or executable_dir)
        if resolved == executable_dir or _is_within(resolved, bundle_dir):
            continue
        forbidden.append(str(resolved))
    return forbidden


def forbidden_module_origins(
    frozen_origins: dict[str, str],
    editable_origins: dict[str, str],
    executable_dir: Path,
    bundle_dir: Path,
) -> list[str]:
    """Reject dependencies borrowed from system Python or a sibling internal dir."""
    executable_dir = executable_dir.resolve()
    bundle_dir = bundle_dir.resolve()
    errors: list[str] = []
    for name, origin in frozen_origins.items():
        resolved = _resolve(origin)
        if not _is_within(resolved, bundle_dir):
            errors.append(f"{name} came from outside the manager bundle: {resolved}")
    for name, origin in editable_origins.items():
        resolved = _resolve(origin)
        if not _is_within(resolved, executable_dir) or _is_within(
            resolved, executable_dir / "internal"
        ):
            errors.append(f"{name} came from an invalid application source: {resolved}")
    return errors


def _module_origins(names: Iterable[str]) -> dict[str, str]:
    origins: dict[str, str] = {}
    for name in names:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if not origin:
            raise RuntimeError(f"Module has no verifiable file origin: {name}")
        origins[name] = str(Path(origin).resolve())
    return origins


def run_frozen_runtime_self_test(report_path: Path) -> bool:
    """Write machine-readable evidence and return whether runtime isolation passed."""
    report_path = report_path.resolve()
    report: dict[str, object] = {"status": "failed", "errors": []}
    errors: list[str] = []
    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Runtime self-test must run from the frozen manager executable")
        executable_dir = Path(sys.executable).resolve().parent
        bundle_value = getattr(sys, "_MEIPASS", "")
        if not bundle_value:
            raise RuntimeError("Frozen manager did not expose sys._MEIPASS")
        bundle_dir = Path(bundle_value).resolve()

        frozen_origins = _module_origins(FROZEN_MODULES)
        editable_origins = _module_origins(EDITABLE_MODULES)
        errors.extend(forbidden_sys_path_entries(sys.path, executable_dir, bundle_dir))
        errors.extend(
            forbidden_module_origins(
                frozen_origins,
                editable_origins,
                executable_dir,
                bundle_dir,
            )
        )

        import tkinter

        tcl_patchlevel = str(tkinter.Tcl().call("info", "patchlevel"))
        report.update(
            {
                "executable": str(Path(sys.executable).resolve()),
                "bundleDir": str(bundle_dir),
                "sysPath": [str(_resolve(entry or executable_dir)) for entry in sys.path],
                "frozenModuleOrigins": frozen_origins,
                "editableModuleOrigins": editable_origins,
                "tclPatchlevel": tcl_patchlevel,
            }
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    report["errors"] = errors
    report["status"] = "passed" if not errors else "failed"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return not errors
