# Tool Authoring Guide

This file documents how tools work in the built-in Pigion runner, `platforms/pigion/core/whatchdog.py`, and how to add runtime-specific tools that behave correctly in the generated agent loop.

## Tool Discovery

The generated Pigion runner loads its selected tool docs from its package `exp/td.txt`. Source documentation and tool policy come from the runtime manifest and tool directory, for example:

```text
platforms/pigion/linux/framework.json
platforms/pigion/linux/exp/td.txt
```

The generated `exp/td.txt` serves two purposes:

1. It is injected into the model prompt as the list of available tools.
2. It is parsed by `tool_import()` to decide which Python modules to import.

Each tool line must contain a comma-separated command field in the third position:

```text
Command - toolname:ARGUMENT
```

Example:

```text
6.FileRead, Description: Reads a local text file, Command - fileread:PATH, Example - fileread:README.md
```

For that line, the runner imports:

```python
from local_watchdog.tools.fileread import fileread
```

The parser is currently brittle: avoid extra commas before the `Command - ...` field, and keep the command prefix identical to the Python function name.

Special case: `return` maps to the module name `return_value` during import, but `return:TEXT` is normally handled directly by `run_tool()`.

## Required Tool File Shape

Create a file in:

```text
platforms/pigion/<runtime>/tools/<toolname>.py
```

The file must expose a callable with the same name as the tool prefix:

```python
def toolname(command, memory, local_state, program_state):
    ...
```

The current Pigion runner calls tools like this:

```python
TOOLS[prefix](suffix, memory, local_state, program_state=program_state)
```

That means a compatible tool must accept `program_state` as a keyword argument.

## Required Return Shape

A normal tool should return a dictionary:

```python
{
    "ok": True,
    "output": "text or structured result",
    "memory": memory,
    "state": local_state,
    "program_state": program_state,
}
```

Recommended fields:

```python
{
    "ok": True,
    "output": output,
    "memory": memory,
    "state": local_state,
    "program_state": program_state,
    "interactive_mode": False,
    "completed": True,
}
```

Failure example:

```python
{
    "ok": False,
    "output": "File not found: missing.txt",
    "error": "File not found: missing.txt",
    "memory": memory,
    "state": local_state,
    "program_state": program_state,
    "interactive_mode": False,
    "completed": True,
}
```

The runner mostly reasons over `output`, `state`, `memory`, and `program_state`. If a failure is returned as data instead of an exception, include enough detail in `output` for the evaluator to understand it.

## Directly Handled Actions

Some actions are intercepted in `run_tool()` before dynamic dispatch:

- `return:TEXT` appends to the global `returned_output`.
- `askuser:QUESTION` calls Python `input()` and returns the answer.

Because of that, the platform `return_value.py` and `askuser.py` modules are compatibility files rather than the best examples of the standard dispatched-tool contract. A new tool should follow the signature and return shape above.

With the Gemini provider, the model does not see or emit the internal `toolname:input` form. The runner derives one
native function declaration per loaded tool from `td.txt`, requires exactly one Gemini function call, and converts its
string `input` argument back to the existing internal action immediately before `run_tool()`. SDK automatic function
execution is disabled so evaluation, failure recovery, experience storage, and interactive-mode transitions remain
owned by Pigion. The textual form remains the OpenAI/Ollama compatibility protocol.

## State Rules

Tools receive:

- `command`: the text after `toolname:`.
- `memory`: the current plain-text runtime memory.
- `local_state`: model-visible runtime state.
- `program_state`: loop/tool metadata.

Use `local_state` for facts the model should see in future prompts, such as:

- `CURRENT_WORKING_DIRECTORY`
- `last_action`
- `last_tool_output`
- `MEMORYVALS`

Use `program_state` for implementation metadata the agent does not need to see directly, such as:

- interactive session labels
- branch mode flags
- caches
- internal handles or IDs
- plan bookkeeping such as `plan_status`

Prefer copying `local_state` before mutating it:

```python
def mytool(command, memory, local_state, program_state):
    local_state = dict(local_state)
    local_state["last_tool_output"] = "done"
    return {
        "ok": True,
        "output": "done",
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
    }
```

## Minimal Tool Example

```python
def uppercase(command, memory, local_state, program_state):
    local_state = dict(local_state)
    output = str(command).upper()
    local_state["last_tool_output"] = output

    return {
        "ok": True,
        "output": output,
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
        "interactive_mode": False,
        "completed": True,
    }
```

Add equivalent documentation and a tool-policy entry to the runtime's `framework.json`; keep `exp/td.txt` aligned where a source copy is retained:

```text
6.Uppercase, Description: Converts text to uppercase, Command - uppercase:TEXT, Example - uppercase:hello
```

## Memory Tool Pattern

If a tool updates `memory`, return the new memory string. If it stores structured values, put them in `local_state["MEMORYVALS"]`.

Current `memadd` behavior:

- `memadd:note text` appends to the memory string.
- `memadd:key=value` updates `local_state["MEMORYVALS"][key]`.
- Duplicate last-line plain-text memory writes are ignored.

There is no source `memget` tool in the current Pigion platform tools. If retrieval or embedded substitution is added later, implement it in `run_tool()` before dispatch so it works inside any tool input, and add matching model-facing documentation to the runtime manifest.

The runner does not persist `memory` to disk by default.

## Interactive Mode

Interactive mode is used when a tool cannot finish in one call because it started a long-lived prompt or terminal program. The Pi shell uses this for commands like:

- `ssh`
- `sftp`
- `python`
- `bash`
- `mysql`
- `psql`
- `ftp`
- `vim`
- `nmap`

To enter interactive mode, a tool returns:

```python
{
    "ok": True,
    "output": "prompt or latest terminal output",
    "memory": memory,
    "state": local_state,
    "program_state": program_state,
    "interactive_mode": True,
    "completed": False,
}
```

When the runner sees `interactive_mode: True`, it calls `start_interactive_mode()`. During that loop, the model produces direct input strings for the same tool. The runner calls:

```python
TOOLS[tool](input_text, memory, local_state, program_state=program_state)
```

The tool should send `input_text` into the existing interactive session, not start a new independent session.

When the interactive session ends, return:

```python
{
    "ok": True,
    "output": "final output",
    "memory": memory,
    "state": local_state,
    "program_state": program_state,
    "interactive_mode": False,
    "completed": True,
}
```

When the interactive session ends, the runner asks the LLM to summarize what happened and stores that summary in `program_state["interactive_where_left_off"]`, keyed by the current working-directory identifier. The runner then calls `evaluate_action_interactive()`, which asks the model to evaluate all plan steps. The resulting `plan_status` map is stored in `program_state`, and the loop updates the plan and jumps to the next unfinished step.

## Where-Left-Off Summaries

The Pigion runner keeps interactive-session continuation notes in `program_state` for the current agent run:

```python
program_state["interactive_where_left_off"] = {
    "/home/user/project": {
        "summary": "what was done",
        "completed": ["observed completed work"],
        "remaining": ["likely remaining work"],
        "next_suggested_input": "optional next input",
        "risk_notes": ["optional caution"],
        "goal": "...",
        "current_step": "...",
        "tool": "shell",
        "cwd_identifier": "/home/user/project",
        "saved_at": 1234567890.0,
    }
}
```

At the start of a later interactive session in the same working-directory identifier, the runner asks the LLM whether the previous summary is relevant to the current goal and current step. The summary and decision are printed to the console. If the LLM chooses to continue, shell resumes the saved live branch session when it still exists. If the decider also returns a `resume_input`, the runner sends that input into the resumed branch before the normal interactive loop continues. The execution is recorded in `program_state["interactive_resume_executed"]`.

The Linux shell tool is wired into this store. When a new shell branch session starts in a cwd that has a saved where-left-off summary, `platforms/pigion/linux/tools/shell.py` prepends a compact summary to the first branch output and records shell-specific metadata:

```python
program_state["SHELL_WHERE_LEFT_OFF_CWD"]
program_state["SHELL_WHERE_LEFT_OFF_AVAILABLE"]
program_state["SHELL_WHERE_LEFT_OFF_FORCED_FRESH"]
program_state["SHELL_WHERE_LEFT_OFF_SUMMARY"]
```

The shell does not decide to replay `next_suggested_input` by itself. It exposes the summary and saved branch to the runner and LLM decider. If the decider returns `continue_where_left_off: true`, shell reattaches the saved PTY branch. If the decider returns `continue_where_left_off: false`, shell kills the saved branch and starts a fresh branch from the original launch command.

The working-directory identifier follows the shell tool's decoration rules. A branch cwd like:

```text
sftp_interactive - /home/user/project
```

is treated as:

```text
/home/user/project
```

This means a later `shell` interactive session in the same real directory can see the previous where-left-off summary, even if the active shell session label changes.

## Forcing A Fresh Interactive Start

An interactive-capable tool can override the where-left-off question and force the runner to ignore any previous same-directory summary for the next interactive session. Set one of these one-shot flags in `program_state` before returning `interactive_mode: True`:

```python
program_state["force_interactive_start_fresh"] = True
```

Compatibility aliases are also accepted:

```python
program_state["INTERACTIVE_FORCE_START_FRESH"] = True
program_state["interactive_start_fresh"] = True
```

When this flag is set, the runner records `program_state["interactive_resume_decision"]` with `continue_where_left_off: False`, prints that the tool forced a fresh start, clears the flag, and does not ask the LLM whether to resume. If the tool also needs to reset a PTY, socket, remote connection, or other handle, it should do that cleanup itself before returning control to the runner.

## Interactive Tool Requirements

An interactive-capable tool needs these pieces:

- A module-level session handle, process handle, socket, PTY fd, or equivalent.
- A way to decide whether a call starts a new session or continues an existing one.
- A stable session label stored in `program_state` or module state.
- A completion detector.
- A prompt/idle detector for deciding when to return control to the agent.
- A cleanup path.
- Optional: a `program_state["force_interactive_start_fresh"] = True` override when previous same-directory interactive summaries should be ignored.

For shell-like tools, do not derive a new label from every interactive input. If the launch command is `sftp user@host`, the branch should keep a label such as `sftp_interactive` for the whole branch. Passwords, `echo`, `put`, `get`, and other input lines should not create labels like `password_interactive` or `echo_interactive`.

## Current Shell Interactive Design

`platforms/pigion/linux/tools/shell.py` has two PTYs:

- Main shell PTY for ordinary commands.
- Branch shell PTY for interactive programs.

Important module-level state:

```python
_SHELL_PID
_SHELL_FD
_BRANCH_PID
_BRANCH_FD
_BRANCH_MARKER
_BRANCH_SESSION_LABEL
_INTERACTIVE_MODE
```

Launch flow:

1. `_should_use_branch(command)` detects known interactive launchers.
2. `_start_branch_shell(cwd)` creates a branch PTY.
3. The command is sent to the branch.
4. `_read_until_marker_or_idle_fd()` reads until completion, prompt, or idle.
5. `_branch_session_label()` chooses a stable label such as `sftp_interactive` or `@host`.
6. The tool returns `interactive_mode: True` when the branch is still active.

Continuation flow:

1. `_INTERACTIVE_MODE` and branch liveness indicate continuation.
2. The input text is sent to `_BRANCH_FD`.
3. The original `_BRANCH_SESSION_LABEL` is reused.
4. The tool returns the latest output and whether the branch is still active.

Termination and detach:

- Input `done` detaches and saves a still-running branch shell under `program_state["SHELL_INTERACTIVE_SESSION_NAME"]` instead of killing it.
- A later same-session launch can resume that saved PTY if the decider chooses `continue_where_left_off: true`.
- If the decider chooses a fresh start, shell kills the saved branch and launches a new branch.
- Special keys such as `SIGINT`, `EOF`, and `SIGTSTP` are supported by the shell tool.
- `shell_reset()` cleans up main and branch shells.

## Working Directory Decoration

The shell tool decorates `CURRENT_WORKING_DIRECTORY` while a branch session is active:

```text
sftp_interactive - /home/bogdan/Pigion_wrapper/Pigion
```

The real cwd is recovered with `_undecorate_cwd()`. It strips stacked session labels defensively, so old bad values like:

```text
password_interactive - sftp_interactive - echo_interactive - /path
```

collapse back to:

```text
/path
```

## Output Guidelines

Keep tool output useful for the evaluator:

- Include the actual result or a clear error message.
- Avoid returning huge raw logs when a summary or tail is enough.
- Scrub secrets from output.
- Preserve enough command output for debugging.
- Use strings unless the model benefits from structured JSON-like data.

For verbose tools, consider truncation logic like the Linux platform `shell.py`.

## Error Handling

Tools may either:

- Return `ok: False` with an `error` and useful `output`.
- Raise an exception and let the runner fail.

Returning a structured failure is usually better because it lets the evaluator and recovery logic see the problem.

Example:

```python
def fileread(command, memory, local_state, program_state):
    local_state = dict(local_state)
    path = str(command).strip()

    try:
        with open(path, "r", encoding="utf-8") as f:
            output = f.read()
    except OSError as e:
        output = f"{type(e).__name__}: {e}"
        local_state["last_tool_output"] = output
        return {
            "ok": False,
            "output": output,
            "error": output,
            "memory": memory,
            "state": local_state,
            "program_state": program_state,
            "interactive_mode": False,
            "completed": True,
        }

    local_state["last_tool_output"] = output
    return {
        "ok": True,
        "output": output,
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
        "interactive_mode": False,
        "completed": True,
    }
```

## Tool Checklist

Before adding a tool:

- Add `platforms/pigion/<runtime>/tools/<toolname>.py`.
- Define `def toolname(command, memory, local_state, program_state):`.
- Return `ok`, `output`, `memory`, `state`, and `program_state`.
- Update `last_tool_output` when useful.
- Use `program_state` for internal metadata.
- Add the tool, its `Command - toolname:...` documentation, and its required/default/optional policy to `platforms/pigion/<runtime>/framework.json`.
- Keep the `td.txt` command prefix identical to the function name.
- Keep the `Command - ...` field as the third comma-separated field.
- Run `python3 -m py_compile platforms/pigion/<runtime>/tools/<toolname>.py platforms/pigion/core/whatchdog.py`.

For interactive tools:

- Persist session handles across calls.
- Return `interactive_mode: True` and `completed: False` while waiting for more input.
- Reuse the launch session label across continuation calls.
- Return `interactive_mode: False` and `completed: True` when done.
- Provide a cleanup/reset path.

## ShadowFS TODO

ShadowFS is not implemented yet. The intended direction is to stage agent file operations in a shadow filesystem layer, expose diffs or pending writes for inspection, and commit or discard those changes explicitly. It must cover the framework runner and relevant tools rather than existing only inside one runtime's `shell.py`.

## Current Tool Compatibility Notes

- `shell`, `memadd`, and the normal path of `search` follow the current `program_state`-aware contract.
- `askuser:` is handled directly by `run_tool()`; the platform `askuser.py` files are compatibility modules.
- `return:` is handled directly by `run_tool()`, while `return_value.py` is mostly a compatibility stub.
- `search` has one retry-success path that returns without `program_state`.
- `memget` is not currently available as a source platform tool.
