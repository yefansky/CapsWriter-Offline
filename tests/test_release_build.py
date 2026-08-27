import runpy
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from verify_manager_bundle import (  # noqa: E402
    REQUIRED_MODULES,
    missing_required_modules,
    normalise_module_name,
)
from smoke_manager_layout import run_manager_smoke  # noqa: E402


class ReleaseBuildTest(unittest.TestCase):
    def run_build_hook(self, executable_dir: Path, meipass: Path) -> list[str]:
        original_executable = sys.executable
        original_path = sys.path.copy()
        missing = object()
        original_meipass = getattr(sys, "_MEIPASS", missing)
        try:
            sys.executable = str(executable_dir / "program.exe")
            sys._MEIPASS = str(meipass)
            runpy.run_path(str(ROOT_DIR / "build_hook.py"))
            return sys.path[:2]
        finally:
            sys.executable = original_executable
            sys.path[:] = original_path
            if original_meipass is missing:
                del sys._MEIPASS
            else:
                sys._MEIPASS = original_meipass

    def test_onefile_manager_does_not_prefer_sibling_internal(self):
        with tempfile.TemporaryDirectory() as directory:
            executable_dir = Path(directory) / "release"
            internal_dir = executable_dir / "internal"
            manager_bundle = Path(directory) / "_MEI-manager"
            internal_dir.mkdir(parents=True)
            manager_bundle.mkdir()

            path_prefix = self.run_build_hook(executable_dir, manager_bundle)

        self.assertEqual(path_prefix[0], str(executable_dir))
        self.assertNotEqual(path_prefix[0], str(internal_dir))

    def test_onedir_program_keeps_its_own_internal(self):
        with tempfile.TemporaryDirectory() as directory:
            executable_dir = Path(directory) / "release"
            internal_dir = executable_dir / "internal"
            internal_dir.mkdir(parents=True)

            path_prefix = self.run_build_hook(executable_dir, internal_dir)

        self.assertEqual(path_prefix[:2], [str(internal_dir), str(executable_dir)])

    def test_layout_smoke_runs_manager_stop_from_release_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory) / "CapsWriter-Offline"
            manager = release_dir / "start_manager.exe"
            (release_dir / "internal").mkdir(parents=True)
            manager.touch()
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with patch("smoke_manager_layout.subprocess.run", return_value=completed) as run:
                run_manager_smoke(manager, timeout=7)

        command = run.call_args.args[0]
        self.assertEqual(command, [str(manager.resolve()), "--stop"])
        self.assertEqual(run.call_args.kwargs["cwd"], manager.parent.resolve())
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_layout_smoke_rejects_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            release_dir = Path(directory) / "CapsWriter-Offline"
            manager = release_dir / "start_manager.exe"
            (release_dir / "internal").mkdir(parents=True)
            manager.touch()
            completed = subprocess.CompletedProcess([], 1, stdout="", stderr="import failed")
            with patch("smoke_manager_layout.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "import failed"):
                    run_manager_smoke(manager)

    def test_archive_entry_names_are_normalised(self):
        self.assertEqual(
            normalise_module_name(r"tkinter\simpledialog.pyc"),
            "tkinter.simpledialog",
        )
        self.assertEqual(normalise_module_name("tkinter/ttk.py"), "tkinter.ttk")

    def test_required_tk_modules_are_checked(self):
        archive_entries = {
            r"tkinter\messagebox.pyc",
            r"tkinter\simpledialog.pyc",
            r"tkinter\ttk.pyc",
        }
        self.assertEqual(missing_required_modules(archive_entries), set())
        archive_entries.remove(r"tkinter\simpledialog.pyc")
        self.assertEqual(
            missing_required_modules(archive_entries),
            {"tkinter.simpledialog"},
        )

    def test_manager_spec_explicitly_collects_required_tk_modules(self):
        spec_text = (ROOT_DIR / "build-manager.spec").read_text(encoding="utf-8")
        for module_name in REQUIRED_MODULES:
            self.assertIn(repr(module_name), spec_text)

    def test_release_workflow_verifies_frozen_manager(self):
        workflow_text = (ROOT_DIR / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            r"python scripts\verify_manager_bundle.py dist\start_manager.exe",
            workflow_text,
        )

    def test_release_workflow_starts_manager_from_complete_layout(self):
        workflow_text = (ROOT_DIR / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            r"python scripts\smoke_manager_layout.py dist\CapsWriter-Offline\start_manager.exe",
            workflow_text,
        )


if __name__ == "__main__":
    unittest.main()
