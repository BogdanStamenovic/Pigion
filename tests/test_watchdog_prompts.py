import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
PIGION_WATCHDOG = ROOT / "platforms" / "pigion" / "core" / "whatchdog.py"
RESEARCH_WATCHDOG = ROOT / "platforms" / "gpt_researcher" / "core" / "whatchdog.py"


class FakeSdkValue:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


FAKE_GENAI_TYPES = SimpleNamespace(
    FunctionDeclaration=FakeSdkValue,
    Tool=FakeSdkValue,
    AutomaticFunctionCallingConfig=FakeSdkValue,
    ToolConfig=FakeSdkValue,
    FunctionCallingConfig=FakeSdkValue,
    FunctionCallingConfigMode=SimpleNamespace(ANY="ANY"),
    GenerateContentConfig=FakeSdkValue,
)


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


def load_pure_functions(
    source: str,
    names: tuple[str, ...],
    tool_docs: str,
    extra_namespace: dict[str, Any] | None = None,
):
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": __import__("typing").Optional,
        "TOOL_DOCS": tool_docs,
        "re": __import__("re"),
        "types": FAKE_GENAI_TYPES,
    }
    namespace.update(extra_namespace or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PIGION_WATCHDOG), "exec"), namespace)
    return namespace


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
            "build_native_action_system_prompt": "ACTION_ARCHITECTURE",
            "build_recovery_system_prompt": "RECOVERY_ARCHITECTURE",
            "build_native_recovery_system_prompt": "RECOVERY_ARCHITECTURE",
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

        for builder in (
            "build_action_system_prompt",
            "build_native_action_system_prompt",
            "create_plan",
        ):
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

    def test_native_tool_specs_come_from_loaded_tool_docs_without_placeholders(self):
        docs = (
            "1.Shell, Description: Execute a command, Command - shell:COMMAND, Example - shell:echo hi\n"
            "2.Search, Description: Search the web, Command - search:QUERY_OR_URL, Example - search:test"
        )
        functions = load_pure_functions(self.pigion, ("native_tool_specs",), docs)
        specs = functions["native_tool_specs"](docs)
        self.assertEqual([item["name"] for item in specs], ["shell", "search"])
        self.assertEqual(specs[0]["description"], "Execute a command")
        self.assertNotIn("COMMAND", str(specs))
        self.assertNotIn("QUERY_OR_URL", str(specs))

    def test_native_calls_normalize_to_existing_control_loop_shapes(self):
        docs = "Shell, Description: Execute a command, Command - shell:COMMAND"
        names = (
            "native_tool_specs",
            "_function_call_data",
            "normalize_native_action",
            "normalize_native_recovery",
            "normalize_native_interactive",
        )
        functions = load_pure_functions(self.pigion, names, docs)

        action = functions["normalize_native_action"](
            SimpleNamespace(name="shell", args={"input": "pwd"}), docs
        )
        self.assertEqual(action["status"], "ongoing")
        self.assertEqual(action["next_action"], "shell:pwd")

        done = functions["normalize_native_action"](
            SimpleNamespace(name="finish_step", args={"reason": "Observed result."}), docs
        )
        self.assertEqual(done, {"status": "done", "reason": "Observed result.", "next_action": ""})

        retry = functions["normalize_native_recovery"](
            SimpleNamespace(name="shell", args={"input": "python3 fallback.py"}), "Run task", docs
        )
        self.assertEqual(retry["recovery"], "retry")
        self.assertEqual(retry["retry_action"], "shell:python3 fallback.py")

        interactive = functions["normalize_native_interactive"](
            SimpleNamespace(name="shell", args={"input": "continue"}), "shell"
        )
        self.assertEqual(interactive["INPUT"], "continue")

    def test_native_declarations_use_one_string_argument_and_reject_control_collisions(self):
        docs = "Shell, Description: Execute a command, Command - shell:COMMAND"
        names = (
            "native_tool_specs",
            "_native_tool_declarations",
            "_control_declaration",
            "native_action_declarations",
            "native_recovery_declarations",
            "native_interactive_declarations",
        )
        functions = load_pure_functions(self.pigion, names, docs)
        declarations = functions["native_action_declarations"](docs)
        self.assertEqual([item.name for item in declarations], ["shell", "finish_step", "fail_step"])
        shell_schema = declarations[0].parameters_json_schema
        self.assertEqual(shell_schema["required"], ["input"])
        self.assertEqual(shell_schema["properties"]["input"]["type"], "string")

        conflicting = "Control, Description: Conflict, Command - finish_step:TEXT"
        with self.assertRaises(ValueError):
            functions["native_action_declarations"](conflicting)

    def test_native_caller_disables_sdk_execution_and_returns_one_call(self):
        recorded: dict[str, Any] = {}
        expected_call = SimpleNamespace(name="shell", args={"input": "pwd"})

        class FakeModels:
            def generate_content(self, **kwargs):
                recorded.update(kwargs)
                return SimpleNamespace(function_calls=[expected_call])

        namespace = load_pure_functions(
            self.pigion,
            ("_function_call_data", "call_gemini_function"),
            "Shell, Description: Execute a command, Command - shell:COMMAND",
            {
                "_normalize_provider": lambda provider: provider,
                "LLM_PROVIDER": "gemini",
                "client": SimpleNamespace(models=FakeModels()),
                "_active_model_name": lambda: "gemini-2.5-flash-lite",
                "TEMPERATURE": 0.0,
                "MAX_OUTPUT_TOKENS": 50,
                "MAX_LLM_RETRIES": 1,
                "count_tokens": lambda text: len(text) // 4,
                "safe_json": lambda value: str(value),
                "tokens_used": 0,
                "time": SimpleNamespace(sleep=lambda _seconds: None),
            },
        )
        declaration = FAKE_GENAI_TYPES.FunctionDeclaration(
            name="shell",
            description="Execute a command.",
            parameters_json_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
        )
        actual_call = namespace["call_gemini_function"]("Choose.", "Select one.", [declaration])
        self.assertIs(actual_call, expected_call)
        config = recorded["config"]
        self.assertTrue(config.automatic_function_calling.disable)
        self.assertEqual(config.tool_config.function_calling_config.mode, "ANY")
        self.assertEqual(config.tool_config.function_calling_config.allowed_function_names, ["shell"])

    def test_gemini_native_prompts_do_not_teach_text_tool_syntax(self):
        for builder in ("build_native_action_system_prompt", "build_native_recovery_system_prompt"):
            source = function_source(self.pigion, builder)
            self.assertNotIn("model_tool_docs", source)
            self.assertNotIn("tool:<", source)
            self.assertNotIn("shell:", source)
            self.assertNotIn("Command -", source)
            self.assertNotIn("Return ONLY", source)

        caller = function_source(self.pigion, "call_gemini_function")
        self.assertIn("AutomaticFunctionCallingConfig(disable=True)", caller)
        self.assertIn("FunctionCallingConfigMode.ANY", caller)
        self.assertIn("len(calls) != 1", caller)

        selector = function_source(self.pigion, "decide_next_action")
        self.assertIn('if _normalize_provider(LLM_PROVIDER) == "gemini"', selector)
        self.assertIn("call_gemini_function", selector)
        self.assertIn("call_llm", selector)

        fallback_action = function_source(self.pigion, "build_action_system_prompt")
        fallback_recovery = function_source(self.pigion, "build_recovery_system_prompt")
        self.assertIn("model_tool_docs", fallback_action)
        self.assertIn("model_tool_docs", fallback_recovery)


if __name__ == "__main__":
    unittest.main()
