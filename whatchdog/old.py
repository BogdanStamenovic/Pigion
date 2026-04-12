import os
import time
import json
from typing import Any, Dict, List

from google import genai
from google.genai import types

from tools.shell import shell
from tools.search import search

# =========================
# CONFIG
# =========================
returned_output = ""
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700"))
MAX_ACTIONS_PER_STEP = int(os.getenv("MAX_ACTIONS_PER_STEP", "12"))
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "6"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("MAX_RECOVERY_ATTEMPTS", "6"))
TOKENS_PER_GOAL = 50000
tokens_used = 0
def load_tool_docs(path: str = "td.txt") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No tool docs provided."

def load_env(path: str = "enving.txt") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "No environment info provided."
TOOL_DOCS = load_tool_docs()
ENVING = load_env()

def count_tokens(text: str) -> int:
    # Simple approximation: 1 token ~ 4 characters in English
    return len(text) // 4

def init_client() -> genai.Client:
    # Official SDK can read GEMINI_API_KEY / GOOGLE_API_KEY from env automatically,
    # but we also allow explicit passing when GEMINI_API_KEY exists.
    api_key = ""
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


client = init_client()


# =========================
# HELPERS
# =========================

def trim_history(action_history: List[Dict[str, Any]], keep_last: int = 8) -> List[Dict[str, Any]]:
    if not action_history:
        return []
    return action_history[-keep_last:]


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
        candidate = cleaned[start:end + 1]
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("Expected top-level JSON object.")
        return parsed


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# =========================
# SYSTEM PROMPT
# =========================

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

RUNTIME STATE:
{safe_json(state)}

RECENT ACTION HISTORY:
{safe_json(trim_history(action_history))}
    TOKEN USAGE:
-{tokens_used}/{TOKENS_PER_GOAL} tokens used so far for this goal.
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
    disclaimer = f"\n\nYOU MUST ONLY RETURN JSON IN EXACT SCHEMA REQUESTED. NO MARKDOWN, NO EXPLANATION, NO EXTRA TEXT. STRICTLY ONLY JSON."
    full_prompt = system_prompt + "\n\n" + prompt + disclaimer
    added_tokens = count_tokens(full_prompt) + MAX_OUTPUT_TOKENS
    last_error = None

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

Return ONLY:
{{
  "recovery": "retry | replace_step | skip_step | abort_goal",
  "new_step": "...",
  "reason": "..."
}}

RULES:
- Use "retry" if the step can still be done with a different next action.
- Use "replace_step" only if the CURRENT STEP description should be rewritten.
- Use "skip_step" only if it is truly unnecessary or already effectively complete.
- Use "abort_goal" only if the goal cannot continue safely.
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
    if action.startswith("return:"):
        output = action[len("return:"):].strip()
        local_state["last_action"] = action
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
        query = action[len("search:"):].strip()
        # For simplicity, we just echo the search query as output.
        # In a real implementation, you'd call your search tool here.
        output = search(query)
        local_state["last_action"] = action
        local_state["last_tool_output"] = output
        return {
            "output": output,
            "memory": memory,
            "state": local_state,
        }
    if action.startswith("shell:"):
        command = action[len("shell:"):].strip()
        output = shell(command)
        local_state["last_action"] = action
        local_state["last_tool_output"] = output
        # removed last_shell_command/last_shell_output keys
        return {
            "ok": True,
            "output": output,
            "memory": memory,
            "state": local_state,
        }

    if action.startswith("memadd:"):
        value = action[len("memadd:"):].strip()

        existing_lines = [line for line in memory.splitlines() if line.strip()]
        if existing_lines and existing_lines[-1] == value:
            new_memory = memory
            output = "MEMORY_ALREADY_ENDED_WITH_SAME_VALUE"
        else:
            new_memory = (memory + "\n" + value).strip() if memory else value
            output = "MEMORY_UPDATED"

        memory_items = list(local_state.get("memory_items", []))
        if value not in memory_items:
            memory_items.append(value)

        local_state["memory_items"] = memory_items
        local_state["last_action"] = action
        local_state["last_tool_output"] = output
        

        return {
            "ok": True,
            "output": output,
            "memory": new_memory,
            "state": local_state,
        }

    local_state["last_action"] = action
    local_state["last_tool_output"] = "UNKNOWN_TOOL"
    return {
        "ok": False,
        "output": "UNKNOWN_TOOL",
        "memory": memory,
        "state": local_state,
    }


# =========================
# MAIN LOOP
# =========================

def run_agent(goal: str):
    memory = ""
    state: Dict[str, Any] = {
        "memory_items": [],
        "last_action": None,
        "last_tool_output": None,
    }

    print("\n🚀 START: Initializing agent for goal")
    print("📋 PLAN GENERATION: Creating plan framework")

    steps = create_plan(goal, memory=memory, state=state)
    completed_steps: List[str] = []

    print("PLAN:", steps)

    current_step_index = 0

    while current_step_index < len(steps):
        if tokens_used >= TOKENS_PER_GOAL:
            raise RuntimeError(f"Token limit exceeded for goal: {tokens_used}/{TOKENS_PER_GOAL} tokens used")
        current_step = steps[current_step_index]
        action_history: List[Dict[str, Any]] = []
        recovery_attempts = 0
        step_done = False

        print(f"\n➡️ STEP {current_step_index + 1}: {current_step}")

        for round_index in range(MAX_ACTIONS_PER_STEP):
            print(f"🔄 ACTION ROUND {round_index + 1}/{MAX_ACTIONS_PER_STEP} tokens_used={tokens_used}")
            if tokens_used >= TOKENS_PER_GOAL:
                raise RuntimeError(f"Token limit exceeded for goal: {tokens_used}/{TOKENS_PER_GOAL} tokens used")
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
            reason = decision.get("reason", "")
            next_action = str(decision.get("next_action", "")).strip()

            if status == "done":
                print(f"✅ STEP DONE: {current_step}")
                if next_action.startswith("return:"):
                    run_tool(next_action, memory, state)
                    
                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if status == "fail":
                print(f"❌ DECISION FAIL: {reason}")

                recovery = recover_step(
                    goal=goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=state,
                    action_history=action_history,
                    error=reason,
                )

                mode = recovery["recovery"]
                print(f"🩹 RECOVERY MODE: {mode} | {recovery['reason']}")

                if mode == "retry":
                    recovery_attempts += 1
                    if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
                        raise RuntimeError(f"Too many recovery attempts for step: {current_step}")
                    continue

                if mode == "replace_step":
                    steps[current_step_index] = recovery.get("new_step", current_step)
                    current_step = steps[current_step_index]
                    recovery_attempts += 1
                    if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
                        raise RuntimeError(f"Too many step replacements for step: {current_step}")
                    action_history.clear()
                    print(f"🔁 STEP REPLACED WITH: {current_step}")
                    continue

                if mode == "skip_step":
                    print(f"⏭️ SKIPPING STEP: {current_step}")
                    completed_steps.append(current_step)
                    current_step_index += 1
                    step_done = True
                    break

                raise RuntimeError(f"Agent aborted goal during step '{current_step}': {recovery['reason']}")

            if not next_action:
                raise RuntimeError(f"Model returned empty next_action while status was '{status}'")

            tool_result = run_tool(next_action, memory, state)
            memory = tool_result["memory"]
            state = tool_result["state"]
            tool_output = tool_result["output"]

            action_history.append({
                "action": next_action,
                "tool_output": tool_output,
            })

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
            eval_reason = evaluation.get("reason", "")

            if eval_status == "done":
                print(f"✅ STEP COMPLETE AFTER ACTION: {next_action}")
                step_done = True
                completed_steps.append(current_step)
                current_step_index += 1
                break

            if eval_status == "fail":
                print(f"❌ ACTION EVALUATION FAIL: {eval_reason}")

                recovery = recover_step(
                    goal=goal,
                    plan=steps,
                    current_step_index=current_step_index,
                    current_step=current_step,
                    completed_steps=completed_steps,
                    memory=memory,
                    state=state,
                    action_history=action_history,
                    error=eval_reason,
                )

                mode = recovery["recovery"]
                print(f"🩹 RECOVERY MODE: {mode} | {recovery['reason']}")

                if mode == "retry":
                    recovery_attempts += 1
                    if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
                        raise RuntimeError(f"Too many recovery attempts for step: {current_step}")
                    continue

                if mode == "replace_step":
                    steps[current_step_index] = recovery.get("new_step", current_step)
                    current_step = steps[current_step_index]
                    recovery_attempts += 1
                    if recovery_attempts > MAX_RECOVERY_ATTEMPTS:
                        raise RuntimeError(f"Too many step replacements for step: {current_step}")
                    action_history.clear()
                    print(f"🔁 STEP REPLACED WITH: {current_step}")
                    continue

                if mode == "skip_step":
                    print(f"⏭️ SKIPPING STEP: {current_step}")
                    completed_steps.append(current_step)
                    current_step_index += 1
                    step_done = True
                    break

                raise RuntimeError(f"Agent aborted goal during step '{current_step}': {recovery['reason']}")

            print("➡️ STEP STILL ONGOING")

        if not step_done:
            raise RuntimeError(
                f"Step did not finish within MAX_ACTIONS_PER_STEP={MAX_ACTIONS_PER_STEP}: {current_step}"
            )
    print(f"Returned output from agent: {returned_output}")
    print(f"\n🏁 GOAL FINISHED: Agent execution completed and used up:{tokens_used}/{TOKENS_PER_GOAL} tokens for this goal.")
    print("\n🧠 FINAL MEMORY:")
    print(memory)
    print("\n📦 FINAL STATE:")
    print(safe_json(state))


# =========================
# RUN
# =========================

if __name__ == "__main__":
    try:
        run_agent(
            "Idi u desktop i nadji pysilon na githubu, gitclonuj ga udji unutra napravi env i instaliraj"       )
    finally:
        client.close()