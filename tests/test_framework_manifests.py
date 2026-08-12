from __future__ import annotations

import json
import os
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

    def test_invalid_source_runtime_rejected(self):
        with self.assertRaises(maker.FrameworkManifestError):
            self.validate(lambda manifest: manifest.update(source_runtime="../windows"))

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
        self.assertIn(("pigion", "macos"), valid)
        self.assertIn(("gpt_researcher", "windows"), valid)

    def test_pigion_defaults_to_all_manifest_tools(self):
        for runtime in ("linux", "macos", "windows"):
            manifest = maker.validated_framework_manifest(runtime, "pigion")
            self.assertEqual(
                maker.default_tools_for_platform(runtime, "pigion"),
                [tool["name"] for tool in manifest["tools"] if tool["policy"] in {"required", "default"}],
            )

    def test_pigion_search_providers_are_exclusive_and_google_is_default(self):
        for runtime in ("linux", "macos", "windows"):
            manifest = maker.validated_framework_manifest(runtime, "pigion")
            specs = {tool["name"]: tool for tool in manifest["tools"]}
            self.assertEqual(specs["google_search"]["exclusive_group"], "web_search")
            self.assertEqual(specs["google_search"]["requires_provider"], "gemini")
            self.assertEqual(specs["search"]["exclusive_group"], "web_search")
            self.assertIn("google_search", maker.default_tools_for_platform(runtime, "pigion"))
            self.assertNotIn("search", maker.default_tools_for_platform(runtime, "pigion"))
            with self.assertRaisesRegex(ValueError, "exclusive group 'web_search'"):
                maker.validate_tool_selection(["search", "google_search"], runtime, "pigion")

    def test_custom_search_can_replace_google_search(self):
        selected = maker.validate_tool_selection(["shell", "search", "return"], "linux", "pigion")
        self.assertEqual(selected, ["shell", "search", "return"])

    def test_gpt_researcher_requires_research(self):
        for runtime in ("linux", "macos", "windows"):
            self.assertEqual(maker.validate_tool_selection([], runtime, "gpt_researcher"), ["research"])

    def test_macos_reuses_declared_unix_sources(self):
        for framework in ("pigion", "gpt_researcher"):
            manifest = maker.validated_framework_manifest("macos", framework)
            self.assertEqual(manifest["source_runtime"], "linux")
            self.assertEqual(manifest["supported_os"], ["macos"])

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

    def test_pigion_google_search_selection_copies_only_grounded_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(maker, "PROJECT_ROOT", Path(temporary)):
                maker.create_instance(
                    "grounded", "linux", ["google_search", "return"], framework="pigion",
                    environment_text="OS: Linux\nTERMINAL: bash\n",
                )
                generated = Path(temporary) / "grounded"
                self.assertTrue((generated / "tools" / "google_search.py").is_file())
                self.assertFalse((generated / "tools" / "search.py").exists())
                self.assertEqual(
                    (generated / "exp" / "tool_import.txt").read_text(),
                    "google_search return\n",
                )

    def test_macos_instance_copies_tools_from_source_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(maker, "PROJECT_ROOT", Path(temporary)):
                maker.create_instance(
                    "mac_agent", "macos", ["shell", "return"], framework="pigion",
                    environment_text="OS: macOS\nTERMINAL: zsh\n",
                )
                generated = Path(temporary) / "mac_agent"
                self.assertTrue((generated / "tools" / "shell.py").is_file())
                self.assertTrue((generated / "tools" / "return_value.py").is_file())
                manifest = json.loads((generated / "exp" / "framework_manifest.json").read_text())
                self.assertEqual(manifest["source_runtime"], "linux")


class ControllerDeployLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.deploy = cls.root / "deploy"

    def test_controller_scripts_live_only_in_deploy(self):
        names = (
            "install.sh",
            "install.ps1",
            "setup.sh",
            "setup.ps1",
            "uninstall.sh",
            "uninstall.ps1",
        )
        for name in names:
            self.assertTrue((self.deploy / name).is_file(), name)
            self.assertFalse((self.root / name).exists(), name)
        for name in ("install.sh", "setup.sh", "uninstall.sh"):
            self.assertTrue(os.access(self.deploy / name, os.X_OK), name)

    def test_setup_and_uninstall_resolve_repository_root(self):
        setup_sh = (self.deploy / "setup.sh").read_text(encoding="utf-8")
        setup_ps1 = (self.deploy / "setup.ps1").read_text(encoding="utf-8")
        uninstall_ps1 = (self.deploy / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn('PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"', setup_sh)
        self.assertIn('cd "$PROJECT_ROOT"', setup_sh)
        self.assertIn('$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path', setup_ps1)
        self.assertIn("Set-Location $ProjectRoot", setup_ps1)
        self.assertIn('Join-Path $ProjectRoot ".pigion-services"', uninstall_ps1)

    def test_deploy_is_explicitly_tracked_and_documented(self):
        ignore = (self.root / ".gitignore").read_text(encoding="utf-8")
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("!/deploy/", ignore)
        self.assertIn("!/deploy/*.sh", ignore)
        self.assertIn("!/deploy/*.ps1", ignore)
        self.assertIn("./deploy/install.sh", readme)
        self.assertIn("./deploy/uninstall.sh", readme)


if __name__ == "__main__":
    unittest.main()
