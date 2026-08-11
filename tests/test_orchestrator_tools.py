from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "orchestrator" / "run_orchestrator.py"


def load_tool_import_functions():
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"tool_import_names", "tool_import"}
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "ABS_PATH": "unused",
        "List": List,
        "NAME": "orchestrator",
        "import_module": None,
        "os": os,
        "re": __import__("re"),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ORCHESTRATOR), "exec"), namespace)
    return namespace


class OrchestratorToolImportTests(unittest.TestCase):
    def test_command_marker_survives_commas_in_description(self):
        functions = load_tool_import_functions()
        docs = (
            "1.Device Arch [ONLINE], Description: Uses shell, memadd, search, return, and askuser, "
            "Command - device_Arch:GOAL, Example - device_Arch:Check system temperature\n"
        )

        self.assertEqual(functions["tool_import_names"](docs), ["device_Arch"])

    def test_current_generated_manifest_is_importable(self):
        functions = load_tool_import_functions()
        docs = (ROOT / "orchestrator" / "exp" / "td.txt").read_text(encoding="utf-8")

        self.assertEqual(functions["tool_import_names"](docs), ["device_Arch"])

    def test_tool_import_loads_module_named_by_command_marker(self):
        functions = load_tool_import_functions()
        loaded = []

        class FakeModule:
            device_Arch = object()

        functions["import_module"] = lambda name: loaded.append(name) or FakeModule
        with tempfile.TemporaryDirectory() as temp_dir:
            exp_dir = Path(temp_dir) / "exp"
            exp_dir.mkdir()
            (exp_dir / "td.txt").write_text(
                "Device Arch, Description: Has commas, in its profile, Command - device_Arch:GOAL\n",
                encoding="utf-8",
            )
            tools = functions["tool_import"](temp_dir)

        self.assertEqual(loaded, ["orchestrator.tools.device_Arch"])
        self.assertIs(tools["device_Arch"], FakeModule.device_Arch)

    def test_missing_command_marker_has_clear_error(self):
        functions = load_tool_import_functions()

        with self.assertRaisesRegex(ImportError, "line 1: missing Command marker"):
            functions["tool_import_names"]("Device Arch, Description: malformed")


if __name__ == "__main__":
    unittest.main()
