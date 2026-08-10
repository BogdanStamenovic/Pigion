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
        self.assertEqual(manifest["architecture_profile"]["capabilities"], [])
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

    def test_architecture_profile_normalizes_provenance(self):
        manifest = self.validate(
            lambda value: value.update(
                architecture_profile={
                    "summary": "A demo device.",
                    "capabilities": ["Run demos"],
                    "known_failure_modes": [
                        {
                            "statement": "Demo can fail",
                            "provenance": "observed",
                            "source": "test run",
                            "match_terms": ["Demo Failure"],
                        }
                    ],
                }
            )
        )
        self.assertEqual(manifest["architecture_profile"]["capabilities"][0]["provenance"], "declared")
        self.assertEqual(manifest["architecture_profile"]["known_failure_modes"][0]["source"], "test run")
        self.assertEqual(manifest["architecture_profile"]["known_failure_modes"][0]["match_terms"], ["demo failure"])

    def test_architecture_profile_rejects_invalid_provenance(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(
                lambda value: value.update(
                    architecture_profile={
                        "capabilities": [{"statement": "Run demos", "provenance": "guessed"}]
                    }
                )
            )

    def test_device_profile_merges_declared_overrides(self):
        manifest = self.validate(
            lambda value: value.update(architecture_profile={"capabilities": ["Run demos"]})
        )
        profile = maker.device_architecture_profile(
            manifest,
            device_name="demo_one",
            device_os="Demo Linux",
            device_terminal="bash",
            selected_tools=["demo"],
            overrides={"physical_constraints": ["Fixed in place"]},
        )
        self.assertEqual(profile["device"]["name"], "demo_one")
        self.assertEqual(profile["capabilities"][0]["source"], "framework manifest")
        self.assertEqual(profile["physical_constraints"][0]["source"], "device registration")
        self.assertIn("Can: Run demos", maker.concise_architecture_profile(profile))
        routed = maker.routing_architecture_profile(profile)
        self.assertEqual(routed["capabilities"][0]["provenance"], "declared")
        self.assertNotIn("terminal", routed["device"])


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
                profile = json.loads((generated / "exp" / "architecture_profile.json").read_text())
                self.assertEqual(profile["device"]["selected_tools"], ["shell", "return"])
                self.assertTrue(profile["known_failure_modes"])


if __name__ == "__main__":
    unittest.main()
