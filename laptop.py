import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from tools_windows.shell import shell
from tools_windows.search import search
from dotenv import load_dotenv

# =========================
# CONFIG
# =========================
returned_output = ""
MEMORY_VALS = {}
load_dotenv()
ABS_PATH = os.getenv("ABS_PATH")
print(ABS_PATH)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700"))
MAX_ACTIONS_PER_STEP = int(os.getenv("MAX_ACTIONS_PER_STEP", "12"))
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "6"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("MAX_RECOVERY_ATTEMPTS", "6"))
TOKENS_PER_GOAL = int(os.getenv("TOKENS_PER_GOAL", "100000"))
EXP_DB_PATH = os.getenv("EXP_DB_PATH", os.path.join(ABS_PATH, "laptop_exp/exp.jsonl"))
SIMILAR_FAILURES_TOP_K = int(os.getenv("SIMILAR_FAILURES_TOP_K", "5"))
print(EXP_DB_PATH)
tokens_used = 0


# =========================
# ENV LOADERS
# =========================
def load_tool_docs(path: str = "laptop_exp/td.txt", abs: str=ABS_PATH) -> str:
    path = os.path.join(abs, path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No tool docs provided."



def load_env(path: str = "laptop_exp/enving.txt", abs: str=ABS_PATH) -> str:
    try:
        path = os.path.join(abs, path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No environment info provided."


TOOL_DOCS = load_tool_docs()
ENVING = load_env()
print (TOOL_DOCS, ENVING)

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
    global MEMORY_VALS
    memory_vals_block = (
    "MEMORY VALUES:\n" + safe_json(MEMORY_VALS)
    if MEMORY_VALS
    else ""
)
    return f"""
You are an autonomous agent.

You MUST always respond in valid JSON.
SYSTEM ENVIRONMENT:
{ENVING}
AVAILABLE TOOLS:
{tool_docs}

GLOBAL GOAL:
{goal}

PLAN FRAMEWORK:
{safe_json(plan)}

CURRENT STEP INDEX:
{current_step_index}

CURRENT STEP:
{current_step}

COMPLETED STEPS:
{safe_json(completed_steps)}

MEMORY:
{memory}
{memory_vals_block}

RUNTIME STATE:
{safe_json(state)}

RECENT ACTION HISTORY:
{safe_json(trim_history(action_history))}

TOKEN USAGE:
- {tokens_used}/{TOKENS_PER_GOAL} tokens used so far for this goal.

CORE EXECUTION RULES:
- You have a BUDGET OF {TOKENS_PER_GOAL} TOKENS for the entire GOAL. Use them wisely.
- There are NO subplans.
- The PLAN FRAMEWORK is high-level guidance only.
- You must work ONLY on the CURRENT STEP.
- Do NOT perform work that belongs to future steps.
- Return ONLY ONE next executable action at a time when asked.
- After each tool result, re-evaluate whether the CURRENT STEP is complete.
- If the CURRENT STEP is already complete, return status "done".
- If the CURRENT STEP is blocked, return status "fail".
- Do NOT skip ahead.
- Do NOT optimize by doing multiple future steps early.
- Do NOT assume hidden memory. Use only GOAL, PLAN FRAMEWORK, MEMORY, STATE, and ACTION HISTORY.
- If you need something remembered, use the memory tool syntax (example: memadd:some value).
- You have LIMITED state history, if a step takes many actions, you may forget early ones.
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
- NEVER RETURN ANYTHING OTHER THAN THE JSON SCHEMA REQUESTED.
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
    action_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if state.get("force_next_action") != None:
        helper = state["force_next_action"]
        state["force_next_action"] = None
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

    prompt = f"""
Choose the SINGLE next executable action for the CURRENT STEP.

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
"""
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
- If the action failed, violated stepscope or caused an error, return "fail".
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
  "retry_step": "...",
  "new_step": "...",
  "reason": "..."
}}

RULES:
- Use "retry" if the step can still be done with a different next action.
- Use "replace_step" only if the CURRENT STEP description should be rewritten.
- Use "skip_step" only if it is truly unnecessary or already effectively complete.
- Use "abort_goal" only if the goal cannot continue safely.
- Prefer alternatives that resemble successful past recoveries when relevant.
"""
    result = call_llm(prompt, system)
    result.setdefault("recovery", "abort_goal")
    result.setdefault("new_step", current_step)
    result.setdefault("reason", "No reason provided")
    return result


# =========================
# TOOL EXECUTION
# =========================
def run_tool(action: str, memory: str, state: Dict[str, Any]) -> Dict[str, Any]:
    print(f"🔧 Executing: {action}")

    local_state = dict(state)
    local_state["last_action"] = action
    if action.startswith("askuser:"):
        output = input(action[len("askuser:") :].strip())
        local_state["last_tool_output"] = output
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
        }
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
        }

    if action.startswith("search:"):
        query = action[len("search:") :].strip()
        output = str(search(query))
        local_state["last_tool_output"] = output
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
        }

    if action.startswith("shell:"):
        command = action[len("shell:") :].strip()
        output = str(shell(command))
        local_state["last_tool_output"] = output
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
        }

    if action.startswith("memadd:"):
        value = action[len("memadd:") :].strip()
        existing_lines = [line for line in memory.splitlines() if line.strip()]
        if existing_lines and existing_lines[-1] == value:
            new_memory = memory
            output = "MEMORY_ALREADY_ENDED_WITH_SAME_VALUE"
        elif "=" in value:
            global MEMORY_VALS
            key, val = value.split("=", 1)
            MEMORY_VALS[key] = val
            new_memory = memory
            output = f"MEMORY_KEY_UPDATED: {key} to {val}"
        else:
            new_memory = (memory + "\n" + value).strip() if memory else value
            output = "MEMORY_UPDATED"

        local_state["last_tool_output"] = output
        return {
            "ok": True,
            "output": output,
            "memory": new_memory,
            "state": local_state,
        }

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
    state: Dict[str, Any],
    successful_action: str,
) -> None:
    pending = state.get("pending_failure")
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
    state["pending_failure"] = None
    state["last_exp_write"] = {
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
        return {
            "force_next_action": recovery.get("retry_step", None),
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
    global returned_output, tokens_used, MEMORY_VALS
    returned_output = ""
    tokens_used = 0

    exp_store = ExpStore(EXP_DB_PATH)
    memory = ""
    state: Dict[str, Any] = {
        "memory": memory,
        "MEMORYVALS": MEMORY_VALS,
        "last_action": None,
        "last_tool_output": None,
        "pending_failure": None,
        "last_similar_failures": [],
        "exp_db_path": EXP_DB_PATH,
        "exp_cache_loaded": len(exp_store.entries),
        "force_next_action": None,
    }

    print("\n🚀 START: Initializing agent for goal")
    print("📋 PLAN GENERATION: Creating plan framework")

    steps = create_plan(goal, memory=memory, state=state)
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

        print(f"\n➡️ STEP {current_step_index + 1}: {current_step}")

        for round_index in range(MAX_ACTIONS_PER_STEP):
            print(
                f"🔄 ACTION ROUND {round_index + 1}/{MAX_ACTIONS_PER_STEP} tokens_used={tokens_used}"
            )

            if tokens_used >= TOKENS_PER_GOAL:
                raise RuntimeError(
                    f"Token limit exceeded for goal: {tokens_used}/{TOKENS_PER_GOAL} tokens used"
                )

            decision = decide_next_action(
                goal=goal,
                plan=steps,
                current_step_index=current_step_index,
                current_step=current_step,
                completed_steps=completed_steps,
                memory=memory,
                state=state,
                action_history=action_history,
            )

            status = str(decision.get("status", "")).strip().lower()
            reason = str(decision.get("reason", "No reason provided"))
            next_action = str(decision.get("next_action", "")).strip()

            if status == "done":
                print(f"✅ STEP DONE: {current_step}")
                if next_action.startswith("return:"):
                    tool_result = run_tool(next_action, memory, state)
                    memory = tool_result["memory"]
                    state = tool_result["state"]
                    finalize_experience_if_needed(exp_store, state, next_action)
                else:
                    state["pending_failure"] = None

                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if status == "fail":
                print(f"❌ DECISION FAIL: {reason}")
                recovery = recover_from_failure(
                    goal=goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=state,
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
                    state=state,
                )
                if "force_next_action" in recovery_result:
                    state["force_next_action"] = recovery_result["force_next_action"]
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

            tool_result = run_tool(next_action, memory, state)
            memory = tool_result["memory"]
            state = tool_result["state"]
            tool_output = str(tool_result["output"])

            action_history.append(
                {
                    "action": next_action,
                    "tool_output": tool_output,
                }
            )

            evaluation = evaluate_action(
                goal=goal,
                plan=steps,
                current_step_index=current_step_index,
                current_step=current_step,
                completed_steps=completed_steps,
                memory=memory,
                state=state,
                action_history=action_history,
                action=next_action,
                tool_output=tool_output,
            )

            eval_status = str(evaluation.get("status", "")).strip().lower()
            eval_reason = str(evaluation.get("reason", "No reason provided"))
            print(MEMORY_VALS)
            if eval_status == "done":
                print(f"✅ STEP COMPLETE AFTER ACTION: {next_action}")
                finalize_experience_if_needed(exp_store, state, next_action)
                state["exp_cache_loaded"] = len(exp_store.entries)
                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if eval_status == "fail":
                print(f"❌ ACTION EVALUATION FAIL: {eval_reason}")
                recovery = recover_from_failure(
                    goal=goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=state,
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
                    state=state,
                )
                if "force_next_action" in recovery_result:
                    state["force_next_action"] = recovery_result["force_next_action"]

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

    print(f"Returned output from agent: {returned_output}")
    print(
        f"\n🏁 GOAL FINISHED: Agent execution completed and used up:{tokens_used}/{TOKENS_PER_GOAL} tokens for this goal."
    )
    print("\n🧠 FINAL MEMORY:")
    print(memory)
    print("\n📊 MEMORY VALUES:")
    print(MEMORY_VALS)
    print("\n📦 FINAL STATE:")
    print(safe_json(state))


# =========================
# RUN
# =========================
if __name__ == "__main__":
    try:
        run_agent(
            "Go into the test folder, and then sort the files based on their types.")
    finally:
        try:
            client.close()
        except Exception:
            pass
