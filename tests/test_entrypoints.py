"""Verify supported command-line entry points without opening a picker."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from code_editors.cli import main


class EntrypointTests(unittest.TestCase):
    def test_new_folder_options_reach_configuration(self):
        with patch("code_editors.cli.process_folder", return_value=["Saved"]) as process:
            self.assertEqual(main(["project", "--folder", "--literal-mode", "polymorphic",
                "--extended-flow", "--rename-modules", "--report", "result.json",
                "--verify-entry", "main.py", "--verify-timeout", "4"]), 0)
        config = process.call_args.args[2]
        self.assertTrue(config.rename_modules)
        self.assertTrue(config.extended_flow)
        self.assertEqual(config.literal_mode, "polymorphic")
        self.assertEqual(config.report_path, Path("result.json"))
        self.assertEqual(config.verify_timeout, 4)
        self.assertEqual(config.verify_entry, "main.py")

    def test_all_selects_strong_settings_and_preserves_routing(self) -> None:
        with patch("code_editors.cli.process_file", return_value=["Saved"]) as process:
            self.assertEqual(main(["app.py", "--all", "--seed", "7", "--layers", "3"]), 0)
        source, destination, config = process.call_args.args
        self.assertEqual(source, Path("app.py"))
        self.assertIsNone(destination)
        self.assertEqual(config.seed, 7)
        self.assertEqual(config.layers, 3)
        self.assertEqual(config.junk_count, 16)
        self.assertEqual(config.name_length, 6)
        self.assertEqual(config.import_mode, "runtime")
        self.assertEqual(config.literal_mode, "polymorphic")
        self.assertTrue(config.extended_flow)
        self.assertEqual(config.dispatch_mode, "table")
        self.assertFalse(config.allow_introspection)
        self.assertEqual(config.stage_order.count("xor"), 2)
        self.assertEqual(config.stage_order.count("name_mangler"), 2)

    def test_script_and_module_expose_the_same_cli(self) -> None:
        root = Path(__file__).resolve().parents[1]
        outputs = []
        for command in (
            [sys.executable, str(root / "pipeline.py"), "--help"],
            [sys.executable, "-m", "code_editors", "--help"],
        ):
            result = subprocess.run(
                command, cwd=root, capture_output=True, text=True, timeout=20
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(*outputs)
