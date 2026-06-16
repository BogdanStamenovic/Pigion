import json
import math
import os  # Kept here
import re
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv
from importlib import import_module

# =========================
# FIX PATH
# =========================
import sys
# Removed the second 'import os' line from here

# Get the directory above the current folder
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Inject it into Python's lookup path if it isn't already there
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# =========================
# CONFIG
# =========================
returned_output = ""
load_dotenv()
NAME = "pi"
ABS_PATH = os.path.join(os.getenv("ABS_PATH"), NAME)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700"))
MAX_ACTIONS_PER_STEP = int(os.getenv("MAX_ACTIONS_PER_STEP", "12"))
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "6"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("MAX_RECOVERY_ATTEMPTS", "6"))
TOKENS_PER_GOAL = int(os.getenv("TOKENS_PER_GOAL", "100000"))
EXP_DB_PATH = os.getenv("EXP_DB_PATH", os.path.join(ABS_PATH, "exp/exp.jsonl"))
SIMILAR_FAILURES_TOP_K = int(os.getenv("SIMILAR_FAILURES_TOP_K", "5"))
print(EXP_DB_PATH)
tokens_used = 0
MEMORY_VALS: Dict[str, Any] = {}
USE_GOAL_FORMALIZER = str(os.getenv("USE_GOAL_FORMALIZER", "True")).lower() in ("1", "true", "yes")
# =========================
# ENV LOADERS
# =========================
def tool_import(abs_path: str = ABS_PATH) -> dict:  # Renamed 'abs' to 'abs_path' to avoid shadowing built-in abs()
    path = os.path.join(abs_path, "exp/td.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            a = f.read()
            # Removed redundant f.close() as 'with' handles it automatically
        to_import = []
        data_lines = a.splitlines()
        for line in data_lines:
            a = line.split(",")
            to_process = a[2]
            to_process = to_process.split(" - ")
            to_process = to_process[1].split(":")
            to_import.append(to_process[0])
        tools = {}
        for imp in to_import:
            if imp == "return":
                imp = "return_value"
            print(f"tools.{imp}")
            tools[imp] = import_module(f"{NAME}.tools.{imp}")
            tools[imp] = getattr(tools[imp], imp)
        return tools
    except FileNotFoundError:
        raise ImportError(f"Tool import file not found at {path}. Ensure that 'tool_import.txt' exists and lists the tools to import.")
def load_tool_docs(path: str = "exp/td.txt", abs: str = ABS_PATH) -> str:
    path = os.path.join(abs, path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No tool docs provided."


def load_env(path: str = "exp/enving.txt", abs: str = ABS_PATH) -> str:
    try:
        path = os.path.join(abs, path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No environment info provided."


TOOL_DOCS = load_tool_docs()
ENVING = load_env()
TOOLS = tool_import(ABS_PATH)
print(TOOL_DOCS, ENVING)


# =========================
# TOKEN / JSON HELPERS
# =========================
def count_tokens(text: str) -> int:
    # Simple approximation: 1 token ~ 4 characters in English.
    return max(1, len(text) // 4)


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def extract_json(raw_text: str) -> Dict[str, Any]:
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("Expected top-level JSON object.")
        return parsed
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Could not extract JSON from model output:\n{raw_text}")
        candidate = cleaned[start : end + 1]
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("Expected top-level JSON object.")
        return parsed



# =========================
# EXPERIENCE STORE
# =========================
def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())



def _vectorize(text: str) -> Dict[str, float]:
    vec: Dict[str, float] = {}
    for token in _tokenize(text):
        vec[token] = vec.get(token, 0.0) + 1.0
    return vec



def _cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0

    dot = 0.0
    for key, value in a.items():
        dot += value * b.get(key, 0.0)

    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


class ExpStore:
    def __init__(self, path: str = EXP_DB_PATH) -> None:
        self.path = path
        self.entries: List[Dict[str, Any]] = []
        self.vectors: List[Dict[str, float]] = []
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._load()

    def _load(self) -> None:
        self.entries = []
        self.vectors = []

        if not os.path.exists(self.path):
            return

        with open(self.path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.entries.append(entry)
                self.vectors.append(
                    _vectorize(
                        " ".join(
                            [
                                str(entry.get("name", "")),
                                str(entry.get("reason", "")),
                                str(entry.get("step", "")),
                                str(entry.get("failed_action", "")),
                            ]
                        )
                    )
                )

    def add_entry(
        self,
        name: str,
        reason: str,
        alternative: str,
        *,
        step: Optional[str] = None,
        failed_action: Optional[str] = None,
        successful_action: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "name": name,
            "reason": reason,
            "alternative": alternative,
            "step": step,
            "failed_action": failed_action,
            "successful_action": successful_action,
            "created_at": time.time(),
        }

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self.entries.append(entry)
        self.vectors.append(
            _vectorize(
                " ".join(
                    [
                        name,
                        reason,
                        step or "",
                        failed_action or "",
                    ]
                )
            )
        )
        return entry

    def find_similar(self, name: str, reason: str, *, step: str = "", failed_action: str = "", top_k: int = 5) -> List[Dict[str, Any]]:
        query_vector = _vectorize(" ".join([name, reason, step, failed_action]))
        scored: List[tuple[float, Dict[str, Any]]] = []

        for entry, vector in zip(self.entries, self.vectors):
            score = _cosine_sparse(query_vector, vector)
            scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)

        results: List[Dict[str, Any]] = []
        for score, entry in scored[:top_k]:
            if score <= 0.0:
                continue
            results.append(
                {
                    "score": round(score, 4),
                    "name": entry.get("name"),
                    "reason": entry.get("reason"),
                    "alternative": entry.get("alternative"),
                    "step": entry.get("step"),
                    "failed_action": entry.get("failed_action"),
                }
            )
        return results



# =========================
# FAILURE CLASSIFICATION
# =========================
def infer_failure_name(action: str, error: str) -> str:
    text = f"{action} {error}".lower()

    if "permission denied" in text:
        return "permission_denied"
    if "repository not found" in text or ("git clone" in text and "not found" in text):
        return "repository_not_found"
    if "module not found" in text:
        return "module_not_found"
    if "no such file" in text or "path not found" in text:
        return "path_not_found"
    if "timeout" in text:
        return "tool_timeout"
    if "rate limit" in text:
        return "rate_limited"
    if "json" in text:
        return "json_parse_failed"
    if "pip" in text or "install" in text and ("dependency" in text or "package" in text):
        return "dependency_install_failed"
    if action.startswith("shell:"):
        return "shell_command_failed"
    if action.startswith("search:"):
        return "search_failed"
    if action.startswith("memadd:"):
        return "memory_write_failed"
    return "generic_step_failure"



# =========================
# CLIENT INIT
# =========================
def init_client() -> genai.Client:
    load_dotenv()
    api_key = os.getenv("API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


client = init_client()



# =========================
# PROMPT BUILDING
# =========================
def trim_history(action_history: List[Dict[str, Any]], keep_last: int = 8) -> List[Dict[str, Any]]:
    if not action_history:
        return []
    return action_history[-keep_last:]


def build_system_prompt(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    tool_docs: str = TOOL_DOCS,
) -> str:
    return f"""
You are an autonomous agent.

You MUST always respond in valid JSON.

SYSTEM ENVIRONMENT:
{ENVING}
AVAILABLE_TOOLS:
{tool_docs}

GLOBAL GOAL:
{goal}

PLAN FRAMEWORK:
{safe_json(plan)}

COMPLETED STEPS:
{safe_json(completed_steps)}

RUNTIME STATE:
{safe_json(state)}

RECENT ACTION HISTORY:
{safe_json(trim_history(action_history))}

CORE EXECUTION RULES:
- The PLAN FRAMEWORK is high-level guidance only.
- Do NOT skip ahead.
- Do NOT optimize by doing multiple future steps early.
- Do NOT assume hidden memory. Use only GOAL, PLAN FRAMEWORK, MEMORY, STATE, and ACTION HISTORY.
- If you need something remembered, use the memory tool syntax (example: memadd:some value).
- If a STEP has a lot of actions, you SHOULD use the MEMORY TOOL to keep track of what you've done and what you know.
- You CANNOT access memadd files trough shell commands, write it yourself.
- Actions must be valid tool commands (example: "shell:cat secret", "memadd:123").

STRICT OUTPUT RULES:
- Output ONLY valid JSON.
- No markdown.
- No explanation outside the requested JSON schema.
- Be concise.
"""



# =========================
# LLM CALL
# =========================
def call_llm(prompt: str, system_prompt: str) -> Dict[str, Any]:
    disclaimer = (
        "\n\nYOU MUST ONLY RETURN JSON IN EXACT SCHEMA REQUESTED. "
        "NO MARKDOWN, NO EXPLANATION, NO EXTRA TEXT. STRICTLY ONLY JSON."
    )
    full_prompt = system_prompt + "\n\n" + prompt + disclaimer
    added_tokens = count_tokens(full_prompt) + MAX_OUTPUT_TOKENS
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )

            raw = (response.text or "").strip()
            print("\n🧠 RAW MODEL OUTPUT:")
            print(raw)

            global tokens_used
            tokens_used += added_tokens

            return extract_json(raw)

        except Exception as e:
            last_error = e
            print(f"Retry {attempt}/{MAX_LLM_RETRIES} zbog: {e}")
            time.sleep(2)

    raise RuntimeError(f"LLM call failed after retries: {last_error}")


def formalize_goal(goal: str) -> str:
    """
    Transform the user goal into a precise, operational task specification.

    Returns plain text only. This function MUST NOT produce JSON, plans,
    numbered steps, tool calls, or add information not implied by the goal.
    """
    system = """
        You are a goal formalizer.

Transform the user's goal into a clearer and more explicit version of the same goal.

Rules:
- Preserve the original objective.
- Do not create a plan.
- Do not create steps.
- Do not suggest tools or commands.
- Do not add new requirements.
- Make implicit assumptions explicit.
- Make locations, files, directories, repositories, URLs, resources, and targets explicit when mentioned.
- Clarify ambiguous references when possible from context.
- Keep the result concise.
- Emphasize the usage of bulk operations. Do not suggest doing things one by one if the goal implies multiple items.
- The output should still read like a goal, not like documentation or a specification.
- Return plain text only.

Example 1:
Input:
Sort the files in D:\Test into folders by extension.

Output:
Go into the folder D:\Test, identify the file type of each file based on its extension, and move each file into a subfolder within D:\Test named after that extension (for example, move "report.pdf" into "D:\Test\pdf\report.pdf"). If a subfolder for an extension does not exist, create it.
Example 2:
Input:
Make a file called test.txt on my desktop and open it.

Output:
Go into the users desktop folder and write the required content into the file called test.txt, and open that desktop file in Notepad.

The output should not contatin implications. Everything implied should be explicitly stated.
For example. If the user says "Sort the files in this folder", you should NOT ommit the directions to change the folder. Nothing should be implied!
    """

    prompt = f"User goal:\n{goal}\n\nReturn the formalized task specification as plain text only."

    added_tokens = count_tokens(system + "\n\n" + prompt) + MAX_OUTPUT_TOKENS
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=system + "\n\n" + prompt,
                config=types.GenerateContentConfig(
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )

            raw = (response.text or "").strip()
            global tokens_used
            tokens_used += added_tokens

            # strip common code fences if the model added them
            if raw.startswith("```"):
                # remove first fence line
                parts = raw.split("\n")
                if len(parts) > 1:
                    parts = parts[1:]
                    if parts and parts[-1].strip().endswith("```"):
                        parts = parts[:-1]
                    raw = "\n".join(parts).strip()

            return raw

        except Exception as e:
            last_error = e
            print(f"Goal formalizer retry {attempt}/{MAX_LLM_RETRIES} due to: {e}")
            time.sleep(1)

    raise RuntimeError(f"Goal formalizer failed after retries: {last_error}")



# =========================
# PLAN
# =========================
def create_plan(goal: str, memory: str, state: Dict[str, Any]) -> List[str]:
    system = build_system_prompt(
        goal=goal,
        plan=[],
        current_step_index=0,
        current_step="planning",
        completed_steps=[],
        memory=memory,
        state=state,
        action_history=[],
    )

    prompt = """
Create a step-by-step plan framework for the GOAL.

Return ONLY:
{
  "steps": ["step 1", "step 2", "step 3"]
}

RULES:
- Steps must be high-level descriptions.
- Steps must NOT be tool calls.
- 3 to 7 steps maximum.
- Do NOT create subplans.
- Do NOT execute anything.
- Do NOT include extra fields.
"""
    result = call_llm(prompt, system)
    steps = result.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"Invalid plan returned: {result}")
    return steps



# =========================
# NEXT ACTION
# =========================
def decide_next_action(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    program_state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    last_eval: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if program_state.get("force_next_action") != None:
        helper = program_state["force_next_action"]
        program_state["force_next_action"] = None
        return {
            "status": "ongoing",
            "reason": "Forced next action from recovery.",
            "next_action": helper,
        }
    system = build_system_prompt(
        goal=goal,
        plan=plan,
        current_step_index=current_step_index,
        current_step=current_step,
        completed_steps=completed_steps,
        memory=memory,
        state=state,
        action_history=action_history,
    )
    # Provide last evaluation context when available
    last_evaluation = ""
    if last_eval:
        last_evaluation = f"""LAST EVALUATION:\nStatus: {last_eval.get('status', 'unknown')}\nReason: {last_eval.get('reason', 'No reason provided')}\n
NOTE: The LAST EVALUATION is only for reference, you MAY override that evaluation/decision if you believe it is incorrect or not applicable. If you override, explain why in the reason field.
"""

    prompt = f"""
Choose the SINGLE next executable action for the CURRENT STEP.

{last_evaluation}

CURRENT STEP:
{current_step}

Return ONLY:
{{
  "status": "ongoing | done | fail",
  "reason": "...",
  "next_action": "shell:... or memadd:..."
}}

RULES:
- Work ONLY on the CURRENT STEP.
- Do NOT perform future steps early.
- If CURRENT STEP is already complete, return status "done" and next_action "".
- If blocked, return status "fail" and next_action "".
- If continuing, return exactly one valid tool action in next_action.
- PREFER actions that can be executed in BULK when using SHELL.
"""
    print(prompt)
    result = call_llm(prompt, system)

    result.setdefault("status", "fail")
    result.setdefault("reason", "No reason provided")
    result.setdefault("next_action", "")

    return result



# =========================
# EVALUATE ACTION
# =========================
def evaluate_action(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    action: str,
    tool_output: str,
) -> Dict[str, Any]:
    system = build_system_prompt(
        goal=goal,
        plan=plan,
        current_step_index=current_step_index,
        current_step=current_step,
        completed_steps=completed_steps,
        memory=memory,
        state=state,
        action_history=action_history,
    )

    prompt = f"""
Evaluate the result of the last action for the CURRENT STEP only.

CURRENT STEP:
{current_step}

LAST ACTION:
{action}

TOOL OUTPUT:
{tool_output}

Return ONLY:
{{
  "status": "ongoing | done | fail",
  "reason": "..."
}}

RULES:
- Mark "done" ONLY if the CURRENT STEP itself is complete.
- Do NOT mark "done" because future-step work was started.
- If the action was useful but the CURRENT STEP is not finished, return "ongoing".
- If the action failed or violated step scope, return "fail".
"""
    result = call_llm(prompt, system)
    result.setdefault("status", "fail")
    result.setdefault("reason", "No reason provided")
    return result



# =========================
# FAILURE RECOVERY
# =========================
def recover_step(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    error: str,
    similar_failures: List[Dict[str, Any]],
) -> Dict[str, Any]:
    print(similar_failures)
    system = build_system_prompt(
        goal=goal,
        plan=plan,
        current_step_index=current_step_index,
        current_step=current_step,
        completed_steps=completed_steps,
        memory=memory,
        state=state,
        action_history=action_history,
    )

    prompt = f"""
The CURRENT STEP encountered a failure.

CURRENT STEP:
{current_step}

ERROR:
{error}

SIMILAR PAST FAILURES:
{safe_json(similar_failures)}

Return ONLY:
{{
  "recovery": "retry | replace_step | skip_step | abort_goal",
  "retry_action": "...",
  "new_step": "...",
  "reason": "..."
}}

RULES:
- Use "retry" if the step can still be done with a different next action.
- Use "replace_step" only if the CURRENT STEP description should be rewritten.
- Use "skip_step" only if it is truly unnecessary or already effectively complete.
- Use "abort_goal" only if the goal cannot continue safely.
- Prefer alternatives that resemble successful past recoveries when relevant.
- Try to consider what the root cause may be and try to validate it.
- Do NOT assume memory to be GROUND TRUTH.
"""
    result = call_llm(prompt, system)
    result.setdefault("recovery", "abort_goal")
    result.setdefault("new_step", current_step)
    result.setdefault("reason", "No reason provided")
    return result

def start_interactive_mode(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    tool: str,
    output: str,
    program_state: Dict[str, Any],) -> Dict[str, Any]:
    local_state = dict(state)
    ExitInteractiveMode = False
    print(f"🔧 INTERACTIVE MODE STARTED for tool: {tool}. Type your input under INPUT: and press Enter. To exit, finish the program or enter ^C or ^Z.")
    action_history.append(
                "INTERACTIVE_MODE_STARTED",
            )
    while not ExitInteractiveMode:
        system = f"""
You are an autonomous agent

You MUST always respond in valid JSON.

GLOBAL GOAL:
{goal}

CURRENT_STEP:
{current_step}

RUNTIME STATE:
{safe_json(state)}

RECENT ACTION HISTORY:
{safe_json(trim_history(action_history))}

RULES:
-Work ONLY on the CURRENT STEP.
-When CURRENT STEP is complete, exit interactive mode by typing done.
"""
        prompt = f"""
INTERACTIVE MODE active for tool: {tool}

All the text you input under INPUT: will be sent to the tool {tool} for execution. The tool's output will be displayed under OUTPUT:.

Return ONLY:
{{
  "reason": "...",
  "INPUT": "..."
}}

RULES:
- All the text you write under INPUT: will be sent directly to the tool {tool} for execution.
- The output will be displayed under OUTPUT:.
- To EXIT interactive mode, finish the program cleanly or type done into the INPUT:.

OUTPUT:
{output}
"""
        
        result = call_llm(prompt, system)
        full = TOOLS[tool](result.get("INPUT", ""), memory, local_state, program_state=program_state)
        output = full.get("output", "")
        if full.get("completed", False):
            ExitInteractiveMode = True
        program_state = full.get("program_state", program_state)
        local_state = full.get("state", local_state)
        action_history.append(
                {
                    "INPUT": result.get("INPUT", ""),
                    "OUTPUT": output,
                }
            )
    action_history.append(
                "INTERACTIVE_MODE_ENDED",
            )
    return {
        "ok": True,
        "output": output,
        "memory": memory,
        "action_history": action_history,
        "program_state": program_state,
        "state": local_state,
    }
# =========================
# TOOL EXECUTION
# =========================
def run_tool(action: str, memory: str, state: Dict[str, Any], program_state: Dict[str, Any]) -> Dict[str, Any]:
    print(f"🔧 Executing: {action}")

    local_state = dict(state)
    local_state["last_action"] = action
    if action.startswith("return:"):
        output = action[len("return:") :].strip()
        local_state["last_tool_output"] = output
        global returned_output
        returned_output = returned_output + output
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
            "program_state": program_state,
        }
    if action.startswith("askuser:"):
        output = input(action[len("askuser:") :].strip())
        local_state["last_tool_output"] = output
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
            "program_state": program_state,
        }

    parts = action.split(":", 1)
    if len(parts) == 2:
        prefix, suffix = parts
        return TOOLS[prefix](suffix, memory, local_state, program_state=program_state)

    local_state["last_tool_output"] = "UNKNOWN_TOOL"
    return {
        "ok": False,
        "output": "UNKNOWN_TOOL",
        "memory": memory,
        "state": local_state,
    }



# =========================
# FAILURE HELPERS
# =========================
def build_pending_failure(
    current_step: str,
    error: str,
    state: Dict[str, Any],
    action_hint: str = "",
) -> Dict[str, Any]:
    failed_action = str(state.get("last_action") or action_hint or "")
    return {
        "name": infer_failure_name(failed_action, error),
        "reason": error,
        "step": current_step,
        "failed_action": failed_action,
        "created_at": time.time(),
    }


def finalize_experience_if_needed(
    exp_store: ExpStore,
    agent_state: Dict[str, Any],
    program_state: Dict[str, Any],
    successful_action: str,
) -> None:
    pending = agent_state.get("pending_failure")
    if not pending or not successful_action:
        return

    exp_store.add_entry(
        name=str(pending.get("name", "generic_step_failure")),
        reason=str(pending.get("reason", "unknown failure")),
        alternative=successful_action,
        step=str(pending.get("step", "")),
        failed_action=str(pending.get("failed_action", "")),
        successful_action=successful_action,
    )
    agent_state["pending_failure"] = None
    program_state["last_exp_write"] = {
        "status": "written",
        "successful_action": successful_action,
        "written_at": time.time(),
    }


def recover_from_failure(
    goal: str,
    plan: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    memory: str,
    state: Dict[str, Any],
    action_history: List[Dict[str, Any]],
    error: str,
    exp_store: ExpStore,
    *,
    action_hint: str = "",
) -> Dict[str, Any]:
    pending_failure = build_pending_failure(
        current_step=current_step,
        error=error,
        state=state,
        action_hint=action_hint,
    )
    state["pending_failure"] = pending_failure

    similar_failures = exp_store.find_similar(
        name=str(pending_failure["name"]),
        reason=str(pending_failure["reason"]),
        step=str(pending_failure.get("step", "")),
        failed_action=str(pending_failure.get("failed_action", "")),
        top_k=SIMILAR_FAILURES_TOP_K,
    )
    state["last_similar_failures"] = similar_failures

    return recover_step(
        goal=goal,
        plan=plan,
        current_step_index=current_step_index,
        current_step=current_step,
        completed_steps=completed_steps,
        memory=memory,
        state=state,
        action_history=action_history,
        error=error,
        similar_failures=similar_failures,
    )



def apply_recovery_decision(
    recovery: Dict[str, Any],
    *,
    steps: List[str],
    current_step_index: int,
    current_step: str,
    completed_steps: List[str],
    action_history: List[Dict[str, Any]],
    recovery_attempts: int,
    state: Dict[str, Any],
) -> Dict[str, Any]:
   
    mode = recovery.get("recovery", "abort_goal")
    print(f"🩹 RECOVERY MODE: {mode} | {recovery.get('reason', '')}")

    if mode == "retry":

        recovery_attempts += 1

        if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            raise RuntimeError(f"Too many recovery attempts for step: {current_step}")
        # If we have a retry action, try to find which past similarity entry
        # corresponds best to that retry. Keep only that entry in state
        # `last_similar_failures` so downstream logic sees the single best match.
        retry_action = recovery.get("retry_action", None)
        try:
            if retry_action:
                candidates = state.get("last_similar_failures") or []
                best: Optional[Dict[str, Any]] = None
                best_score = -1.0
                retry_vec = _vectorize(str(retry_action))

                for entry in candidates:
                    parts = [str(entry.get("alternative", "")), str(entry.get("failed_action", "")), str(entry.get("reason", "")), str(entry.get("step", ""))]
                    candidate_text = " ".join([p for p in parts if p])
                    if not candidate_text:
                        continue
                    score = _cosine_sparse(retry_vec, _vectorize(candidate_text))
                    if score > best_score:
                        best_score = score
                        best = dict(entry)

                if best is not None:
                    best["retry_match_score"] = round(best_score, 4)
                    state["last_similar_failures"] = [best]
                    print(f"🔎 Kept best matching past failure: {best.get('name')} score={best['retry_match_score']}")
                else:
                    state["last_similar_failures"] = []
                    print("🔎 No matching past failure found for retry action; Kept all of them, here they are: ", state["last_similar_failures"])
            else:
                state["last_similar_failures"] = []
        except Exception as e:
            print(f"Error while matching retry action to past failures: {e}")

        return {
            "force_next_action": recovery.get("retry_action", None),
            "recovery_attempts": recovery_attempts,
            "step_done": False,
            "current_step": current_step,
            "advance_step": False,
            "continue_loop": True,
        }

    if mode == "replace_step":

        steps[current_step_index] = recovery.get("new_step", current_step)
        current_step = steps[current_step_index]
        recovery_attempts += 1
        if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            raise RuntimeError(f"Too many step replacements for step: {current_step}")
        action_history.clear()
        print(f"🔁 STEP REPLACED WITH: {current_step}")
        return {
            "recovery_attempts": recovery_attempts,
            "step_done": False,
            "current_step": current_step,
            "advance_step": False,
            "continue_loop": True,
        }

    if mode == "skip_step":
        print(f"⏭️ SKIPPING STEP: {current_step}")
        completed_steps.append(current_step)
        state["pending_failure"] = None
        return {
            "recovery_attempts": recovery_attempts,
            "step_done": True,
            "current_step": current_step,
            "advance_step": True,
            "continue_loop": False,
        }

    raise RuntimeError(
        f"Agent aborted goal during step '{current_step}': {recovery.get('reason', 'No reason provided')}"
    )



# =========================
# MAIN LOOP
# =========================
def run_agent(goal: str) -> None:
    global returned_output, tokens_used
    returned_output = ""
    tokens_used = 0

    exp_store = ExpStore(EXP_DB_PATH)
    memory = ""
    agent_state: Dict[str, Any] = {
        "memory": memory,
        "MEMORYVALS": MEMORY_VALS,
        "last_action": None,
        "last_tool_output": None,
        "pending_failure": None,
        "last_similar_failures": [],
        "CURRENT_WORKING_DIRECTORY": os.getcwd(),
    }
    program_state: Dict[str, Any] = {
        "exp_cache_loaded": len(exp_store.entries),
        "force_next_action": None,
    }

    # Preserve original goal and optionally run the goal formalizer
    original_goal = goal
    formalized_goal = original_goal
    if USE_GOAL_FORMALIZER:
        try:
            formalized_goal = formalize_goal(original_goal)
        except Exception as e:
            print(f"Goal formalizer error, falling back to original goal: {e}")
            formalized_goal = original_goal

    # Store for debugging/inspection
    program_state["original_goal"] = original_goal
    program_state["formalized_goal"] = formalized_goal

    print("\n🚀 START: Initializing agent for goal")
    print("ORIGINAL GOAL:", original_goal)
    print("FORMALIZED GOAL:", formalized_goal)
    print("📋 PLAN GENERATION: Creating plan framework")

    steps = create_plan(formalized_goal, memory=memory, state=agent_state)
    completed_steps: List[str] = []

    print("PLAN:", steps)

    current_step_index = 0
    while current_step_index < len(steps):
        if tokens_used >= TOKENS_PER_GOAL:
            raise RuntimeError(
                f"Token limit exceeded for goal: {tokens_used}/{TOKENS_PER_GOAL} tokens used"
            )

        current_step = steps[current_step_index]
        action_history: List[Dict[str, Any]] = []
        recovery_attempts = 0

        step_done = False
        # Track the last evaluation result so the decider can consider it
        last_eval: Optional[Dict[str, Any]] = None
        print(f"\n➡️ STEP {current_step_index + 1}: {current_step}")

        for round_index in range(MAX_ACTIONS_PER_STEP):
            print(agent_state)
            print(
                f"🔄 ACTION ROUND {round_index + 1}/{MAX_ACTIONS_PER_STEP} tokens_used={tokens_used}"
            )

            if tokens_used >= TOKENS_PER_GOAL:
                raise RuntimeError(
                    f"Token limit exceeded for goal: {tokens_used}/{TOKENS_PER_GOAL} tokens used"
                )
            decision = decide_next_action(
                goal=formalized_goal,
                plan=steps,
                current_step_index=current_step_index,
                current_step=current_step,
                completed_steps=completed_steps,
                memory=memory,
                state=agent_state,
                action_history=action_history,
                program_state=program_state,
                last_eval=last_eval,
            )

            status = str(decision.get("status", "")).strip().lower()
            reason = str(decision.get("reason", "No reason provided"))
            next_action = str(decision.get("next_action", "")).strip()

            if status == "done":
                print(f"✅ STEP DONE: {current_step}")
                if next_action.startswith("return:"):
                    tool_result = run_tool(next_action, memory, agent_state, program_state=program_state)
                    memory = tool_result["memory"]
                    agent_state = tool_result["state"]
                    finalize_experience_if_needed(exp_store, agent_state, program_state, next_action)
                else:
                    agent_state["pending_failure"] = None

                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if status == "fail":
                print(f"❌ DECISION FAIL: {reason}")
                recovery = recover_from_failure(
                    goal=formalized_goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=agent_state,
                    action_history=action_history,
                    error=reason,
                    exp_store=exp_store,
                    action_hint=next_action,
                )

                recovery_result = apply_recovery_decision(
                    recovery,
                    steps=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    action_history=action_history,
                    recovery_attempts=recovery_attempts,
                    state=agent_state,
                )
                if "force_next_action" in recovery_result:
                    program_state["force_next_action"] = recovery_result["force_next_action"]
                recovery_attempts = int(recovery_result["recovery_attempts"])
                current_step = str(recovery_result["current_step"])

                if recovery_result["advance_step"]:
                    current_step_index += 1
                if recovery_result["step_done"]:
                    step_done = True
                    break
                if recovery_result["continue_loop"]:
                    continue

            if not next_action:
                raise RuntimeError(f"Model returned empty next_action while status was '{status}'")

            tool_result = run_tool(next_action, memory, agent_state, program_state=program_state)
            if "interactive_mode" in tool_result and tool_result["interactive_mode"]:
                tool_result = start_interactive_mode(
                    goal=formalized_goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=agent_state,
                    action_history=action_history,
                    tool=next_action.split(":", 1)[0],
                    output=tool_result.get("output", ""),
                    program_state=program_state
                )
                program_state = tool_result.get("program_state", program_state)
                memory = tool_result["memory"]
                agent_state = tool_result["state"]
                tool_output = str(tool_result["output"])
                evaluation = evaluate_action(
                    goal=formalized_goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=agent_state,
                    action_history=action_history,
                    action=next_action,
                    tool_output=tool_output,
                )
            else:
                program_state = tool_result.get("program_state", program_state)
                memory = tool_result["memory"]
                agent_state = tool_result["state"]
                tool_output = str(tool_result["output"])
                action_history.append(
                    {
                        "action": next_action,
                        "tool_output": tool_output,
                    }
                )
                evaluation = evaluate_action(
                    goal=formalized_goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=agent_state,
                    action_history=action_history,
                    action=next_action,
                    tool_output=tool_output,
                )

            # Make the last evaluator output available to the decider on the next round
            last_eval = evaluation

            eval_status = str(evaluation.get("status", "")).strip().lower()
            eval_reason = str(evaluation.get("reason", "No reason provided"))

            if eval_status == "done":
                print(f"✅ STEP COMPLETE AFTER ACTION: {next_action}")
                finalize_experience_if_needed(exp_store, agent_state, program_state, next_action)
                program_state["exp_cache_loaded"] = len(exp_store.entries)
                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if eval_status == "fail":
                print(f"❌ ACTION EVALUATION FAIL: {eval_reason}")
                recovery = recover_from_failure(
                    goal=formalized_goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=agent_state,
                    action_history=action_history,
                    error=eval_reason,
                    exp_store=exp_store,
                    action_hint=next_action,
                )

                recovery_result = apply_recovery_decision(
                    recovery,
                    steps=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    action_history=action_history,
                    recovery_attempts=recovery_attempts,
                    state=agent_state,
                )
                if "force_next_action" in recovery_result:
                    program_state["force_next_action"] = recovery_result["force_next_action"]

                recovery_attempts = int(recovery_result["recovery_attempts"])
                current_step = str(recovery_result["current_step"])

                if recovery_result["advance_step"]:
                    current_step_index += 1
                if recovery_result["step_done"]:
                    step_done = True
                    break
                if recovery_result["continue_loop"]:
                    continue

            print("➡️ STEP STILL ONGOING")

        if not step_done:
            raise RuntimeError(
                f"Step did not finish within MAX_ACTIONS_PER_STEP={MAX_ACTIONS_PER_STEP}: {current_step}"
            )

    print(f"\n\n\n\n\nReturned output from agent: {returned_output}")
    print(
        f"\n🏁 GOAL FINISHED: Agent execution completed and used up:{tokens_used}/{TOKENS_PER_GOAL} tokens for this goal."
    )
    print("\n🧠 FINAL MEMORY:")
    print(memory)
    print("\n📦 FINAL STATE:")
    print(safe_json(agent_state), "\n\n\n\nPROGRAM STATE:", safe_json(program_state))



# =========================
# RUN
# =========================
if __name__ == "__main__":
    try:
        run_agent( 
            "sshpass into bodas@pigion with the password Dobrica111, while inside the server make a file called hi.txt in which you will save the word 'secret' and then sftp inside the pigion server and download the file Hi.txt to the local machine. Then read the contents of the file and return it as output.")
    finally:
        try:
            client.close()
        except Exception:
            pass
