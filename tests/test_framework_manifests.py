from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maker


DOC = "Demo, Description: Demo tool, Command - demo:TEXT, Example - demo:hello"


class FrameworkManifestTests(unittest.TestCase):
    def make_registry(self, mutate=None):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "platforms"
        runtime = root / "demo" / "linux"
        (root / "demo" / "core").mkdir(parents=True)
        (root / "demo" / "core" / "whatchdog.py").write_text("def run_agent(goal): return goal\n")
        (runtime / "tools").mkdir(parents=True)
        (runtime / "tools" / "demo.py").write_text("def demo(*args): return {}\n")
        (runtime / "lifecycle").mkdir()
        (runtime / "lifecycle" / "install.sh").write_text("#!/bin/sh\nexit 0\n")
        manifest = {
            "schema_version": 1,
            "framework": "demo",
            "runtime": "linux",
            "supported_os": ["linux"],
            "runner": "../core/whatchdog.py",
            "allow_zero_tools": False,
            "tools": [{"name": "demo", "module": "demo.py", "policy": "required", "documentation": DOC}],
            "questions": [],
            "lifecycle": {"install": "lifecycle/install.sh"},
        }
        if mutate:
            mutate(manifest)
        (runtime / "framework.json").write_text(json.dumps(manifest))
        return temporary, root

    def validate(self, mutate=None):
        temporary, root = self.make_registry(mutate)
        self.addCleanup(temporary.cleanup)
        with patch.object(maker, "PLATFORMS_ROOT", root):
            return maker.validated_framework_manifest("linux", "demo")

    def test_valid_manifest_and_required_tool(self):
        manifest = self.validate()
        self.assertEqual(manifest["schema_version"], 1)
        temporary, root = self.make_registry()
        self.addCleanup(temporary.cleanup)
        with patch.object(maker, "PLATFORMS_ROOT", root):
            self.assertEqual(maker.validate_tool_selection([], "linux", "demo"), ["demo"])

    def test_duplicate_tools_rejected(self):
        def mutate(manifest):
            manifest["tools"].append(dict(manifest["tools"][0]))
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(mutate)

    def test_invalid_policy_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest["tools"][0].update(policy="sometimes"))

    def test_unsafe_lifecycle_path_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest.update(lifecycle={"install": "../../outside.sh"}))

    def test_missing_tool_module_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest["tools"][0].update(module="missing.py"))

    def test_unsupported_installer_os_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest.update(supported_os=["windows"]))

    def test_invalid_question_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest.update(questions=[{"name": "bad-name"}]))

    def test_invalid_question_validation_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest.update(questions=[{"name": "GOOD_NAME", "validation": {"pattern": "["}}]))


class RepositoryManifestTests(unittest.TestCase):
    def test_all_repository_runtimes_are_valid(self):
        valid, invalid = maker.valid_framework_runtimes()
        self.assertFalse(invalid)
        self.assertIn(("pigion", "linux"), valid)
        self.assertIn(("gpt_researcher", "windows"), valid)

    def test_pigion_defaults_to_all_manifest_tools(self):
        for runtime in ("linux", "windows"):
            manifest = maker.validated_framework_manifest(runtime, "pigion")
            self.assertEqual(
                maker.default_tools_for_platform(runtime, "pigion"),
                [tool["name"] for tool in manifest["tools"]],
            )

    def test_gpt_researcher_requires_research(self):
        for runtime in ("linux", "windows"):
            self.assertEqual(maker.validate_tool_selection([], runtime, "gpt_researcher"), ["research"])

    def test_pigion_subset_generates_only_selected_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(maker, "PROJECT_ROOT", Path(temporary)):
                maker.create_instance(
                    "subset", "linux", ["shell", "return"], framework="pigion",
                    environment_text="OS: Linux\nTERMINAL: bash\n",
                )
                generated = Path(temporary) / "subset"
                self.assertTrue((generated / "tools" / "shell.py").is_file())
                self.assertTrue((generated / "tools" / "return_value.py").is_file())
                self.assertFalse((generated / "tools" / "search.py").exists())
                self.assertEqual((generated / "exp" / "tool_import.txt").read_text(), "shell return\n")


if __name__ == "__main__":
    unittest.main()
