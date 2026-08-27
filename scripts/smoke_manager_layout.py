"""Run the frozen manager from the complete release layout before packaging."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


DEFAULT_TIMEOUT_SECONDS = 60


def run_manager_smoke(executable: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
    executable = executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Manager executable does not exist: {executable}")
    internal_dir = executable.parent / "internal"
    if not internal_dir.is_dir():
        raise RuntimeError(
            "Manager smoke test must run from the complete release layout; "
            f"missing directory: {internal_dir}"
        )

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [str(executable), "--stop"],
            cwd=executable.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Manager did not exit within {timeout}s; an unhandled-exception dialog may be blocking"
        ) from exc

    if completed.returncode != 0:
        details = (
            completed.stderr
            or completed.stdout
            or "<windowed process produced no console output>"
        ).strip()
        raise RuntimeError(f"Manager smoke test failed with exit code {completed.returncode}: {details}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    run_manager_smoke(args.executable, timeout=args.timeout)
    print(f"Manager starts successfully from the complete release layout: {args.executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
