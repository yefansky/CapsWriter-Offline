from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from verify_manager_bundle import (  # noqa: E402
    REQUIRED_MODULES,
    missing_required_modules,
    normalise_module_name,
)


class ReleaseBuildTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
