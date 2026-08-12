from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SHELL_PATH = (
    Path(__file__).resolve().parents[1]
    / "platforms"
    / "pigion"
    / "linux"
    / "tools"
    / "shell.py"
)


def load_shell_module():
    spec = importlib.util.spec_from_file_location("pigion_test_shell", SHELL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NonInteractiveShellTests(unittest.TestCase):
    def setUp(self):
        self.shell = load_shell_module()

    def tearDown(self):
        self.shell.shell_reset()

    def test_interactive_commands_are_rejected_without_starting_shell(self):
        with patch.object(self.shell, "_start_shell") as start_shell:
            result = self.shell.run_shell("python3", stream=False)

        start_shell.assert_not_called()
        self.assertTrue(result["completed"])
        self.assertFalse(result["interactive_mode"])
        self.assertFalse(result["branch_mode"])
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("interactive shell commands are temporarily disabled", result["output"])

    def test_prompt_like_output_cannot_enable_interactive_mode(self):
        result = self.shell.run_shell("printf 'Median: 7\\n>\\n'", stream=False)

        self.assertTrue(result["completed"])
        self.assertFalse(result["interactive_mode"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("Median: 7", result["output"])

    def test_bounded_python_command_still_runs(self):
        result = self.shell.run_shell("python3 -c 'print(2 + 2)'", stream=False)

        self.assertTrue(result["completed"])
        self.assertFalse(result["interactive_mode"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("4", result["output"])


if __name__ == "__main__":
    unittest.main()
