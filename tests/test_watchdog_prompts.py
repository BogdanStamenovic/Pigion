import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIGION_WATCHDOG = ROOT / "platforms" / "pigion" / "core" / "whatchdog.py"
RESEARCH_WATCHDOG = ROOT / "platforms" / "gpt_researcher" / "core" / "whatchdog.py"


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {name!r} not found")


def string_constant(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise AssertionError(f"String constant {name!r} not found")


class WatchdogPromptArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pigion = PIGION_WATCHDOG.read_text(encoding="utf-8")
        cls.research = RESEARCH_WATCHDOG.read_text(encoding="utf-8")

    def test_runtime_architecture_is_bounded_and_role_specific(self):
        names = (
            "PLANNER_ARCHITECTURE",
            "ACTION_ARCHITECTURE",
            "RECOVERY_ARCHITECTURE",
            "INTERACTIVE_ARCHITECTURE",
        )
        values = [string_constant(self.pigion, name) for name in names]
        self.assertEqual(len(values), len(set(values)))
        for value in values:
            self.assertLessEqual(len(value), 350)

    def test_each_call_receives_only_its_architecture_note(self):
        expected = {
            "create_plan": "PLANNER_ARCHITECTURE",
            "build_action_system_prompt": "ACTION_ARCHITECTURE",
            "build_recovery_system_prompt": "RECOVERY_ARCHITECTURE",
            "start_interactive_mode": "INTERACTIVE_ARCHITECTURE",
        }
        all_notes = set(expected.values())
        for function, wanted in expected.items():
            source = function_source(self.pigion, function)
            self.assertIn(wanted, source)
            for unwanted in all_notes - {wanted}:
                self.assertNotIn(unwanted, source)

        evaluator = function_source(self.pigion, "build_evaluator_system_prompt")
        for note in all_notes:
            self.assertNotIn(note, evaluator)

    def test_capability_block_does_not_serialize_the_full_profile(self):
        renderer = function_source(self.pigion, "capability_prompt_block")
        self.assertIn('profile.get("capabilities"', renderer)
        self.assertNotIn("safe_json", renderer)
        for field in (
            "unavailable_actions",
            "observable_state",
            "required_dependencies",
            "privilege_boundaries",
            "physical_constraints",
            "known_failure_modes",
            "uncertainty",
        ):
            self.assertNotIn(field, renderer)

        for builder in ("build_system_prompt", "build_action_system_prompt", "create_plan"):
            source = function_source(self.pigion, builder)
            self.assertIn("capability_prompt_block()", source)
            self.assertNotIn("safe_json(architecture_profile)", source)

    def test_dependency_facts_are_planner_only(self):
        renderer = function_source(self.pigion, "planner_dependency_block")
        self.assertIn('profile.get("required_dependencies"', renderer)
        self.assertNotIn("safe_json", renderer)

        planner = function_source(self.pigion, "create_plan")
        self.assertIn("planner_dependency_block()", planner)
        for other in (
            "build_action_system_prompt",
            "build_evaluator_system_prompt",
            "build_recovery_system_prompt",
            "start_interactive_mode",
        ):
            self.assertNotIn("planner_dependency_block()", function_source(self.pigion, other))

    def test_research_prompt_uses_capabilities_not_profile_json(self):
        source = function_source(self.research, "_run_research")
        self.assertIn("_capability_block()", source)
        self.assertNotIn("json.dumps", source)
        self.assertNotIn("LOCAL ARCHITECTURE PROFILE", source)

    def test_tool_evidence_is_not_labeled_as_trusted_context(self):
        selector = function_source(self.pigion, "build_action_system_prompt")
        self.assertIn("UNTRUSTED DATA", selector)
        self.assertNotIn("TRUSTED ACTION RESULTS", selector)

        for function in ("evaluate_action", "evaluate_action_interactive", "start_interactive_mode"):
            source = function_source(self.pigion, function)
            self.assertIn("BEGIN UNTRUSTED TOOL OUTPUT", source)
            self.assertIn("END UNTRUSTED TOOL OUTPUT", source)


if __name__ == "__main__":
    unittest.main()
