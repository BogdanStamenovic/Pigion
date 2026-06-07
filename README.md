# Pigion

Pigion is a local autonomous-agent framework built around a bounded execution loop called Watchdog. The repository currently contains two platform-specific Watchdog runners:

- `laptop/` for a Windows laptop environment using persistent PowerShell.
- `pi/` for a Raspberry Pi or Linux environment using persistent Bash through a PTY.

Both runners share the same core architecture: create a high-level plan, execute one step at a time, call tools through text actions, evaluate each action, recover from failures when possible, and store useful failure/recovery patterns in a local JSONL experience database.

The codebase is still experimental. Several modules are duplicated between `laptop` and `pi`. This README documents the project exactly as it exists now.

## Repository Layout

```text
Pigion/
  README.md
  maker.py
  requirements.txt
  setup.ps1
  setup.sh
  file_sort_test copy.py
  agent_test_makers/
    file_sort_test.py
  platforms/
    laptop/
      tools/
        askuser.py
        memadd.py
        return_value.py
        search.py
        shell.py
    pi/
      tools/
        askuser.py
        memadd.py
        return_value.py
        search.py
        shell.py
  laptop/
    run_laptop.py
    exp/
      td.txt
      enving.txt
    tools/
      askuser.py
      memadd.py
      return_value.py
      search.py
      shell.py
  pi/
    run_pi.py
    exp/
      td.txt
      tool_import.txt
      enving.txt
    tools/
      askuser.py
      memadd.py
      return_value.py
      search.py
      shell.py
```

## Requirements

Runtime dependencies are listed in `requirements.txt`:

- `google-genai`: Gemini API client used by the Watchdog runners.
- `python-dotenv`: loads `.env` configuration.
- `ddgs`: search and URL extraction tool.
- `protobuf`: dependency used by the Google client stack.

Python 3 is required. The Windows setup script expects `python`; the Unix setup script expects `python3`.

## Installation

Windows PowerShell:

```powershell
.\setup.ps1
```

Linux or Raspberry Pi:

```bash
chmod +x setup.sh
./setup.sh
```

Both scripts can create a `.venv`, install `requirements.txt`, write `.env` values, and write device environment descriptions to `pi/exp/enving.txt` and `laptop/exp/enving.txt`.

## Environment Variables

The runners use these environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEY` | none | Google GenAI API key. |
| `ABS_PATH` | required | Absolute path to the project root. The runner appends `pi` or `laptop`. |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Gemini model used for planning, decisions, evaluation, and recovery. |
| `LLM_TEMPERATURE` | `0.3` | Generation temperature. |
| `MAX_OUTPUT_TOKENS` | `700` | Max tokens requested per model call. |
| `MAX_ACTIONS_PER_STEP` | `12` | Max tool/action rounds per plan step. |
| `MAX_LLM_RETRIES` | `6` | Max model-call retries. |
| `MAX_RECOVERY_ATTEMPTS` | `6` | Max recovery attempts per step. |
| `TOKENS_PER_GOAL` | `100000` | Approximate token budget for one goal. |
| `EXP_DB_PATH` | `<ABS_PATH>/<device>/exp/exp.jsonl` | JSONL experience database path. |
| `SIMILAR_FAILURES_TOP_K` | `5` | Number of similar past failures passed into recovery. |
| `MAX_SHELL_OUTPUT` | `200000` | Linux shell output limit before truncation. |
| `SHELL_TAIL_LINES` | `50` | Linux shell fallback tail length. |
| `SHELL_TRUNCATE_MIN_LINES` | `20` | Linux shell minimum line count before truncation. |
| `SUDO_PASSWORD` | none | Optional sudo password for Linux shell commands. |
| `TEST_SUDO_PASSWORD` | none | Alternate optional sudo password name. |

Example `.env`:

```env
API_KEY="your-google-genai-key"
ABS_PATH="C:\Users\helper\Desktop\Pigion"
GEMINI_MODEL="gemini-2.5-flash-lite"
```

For Linux/Pi:

```env
API_KEY="your-google-genai-key"
ABS_PATH="/home/pi/Pigion"
SUDO_PASSWORD=""
```

## Running Watchdog

Laptop runner:

```powershell
python laptop\run_laptop.py
```

Pi/Linux runner:

```bash
python3 pi/run_pi.py
```

Each runner currently has a hard-coded demo goal in its `if __name__ == "__main__"` block. To run a custom goal, import `run_agent`:

```python
from laptop.run_laptop import run_agent

run_agent("Sort the files in the test folder into subfolders by type.")
```

or:

```python
from pi.run_pi import run_agent

run_agent("Inspect the current directory and summarize what files exist.")
```

## Creating New Instances

Use `maker.py` to generate another Watchdog instance package. The runner logic is copied from the shared Watchdog template and the generated runner gets its own `NAME` value, package folder, tools, and `exp` files.

Platform-specific tool templates live in the central `platforms/` folder:

```text
platforms/
  laptop/
    tools/
  pi/
    tools/
```

To add another platform, create `platforms/<platform-name>/tools` and put compatible tool modules inside it. `maker.py` discovers platform names from that folder.

Interactive mode:

```powershell
python maker.py
```

Interactive mode asks for:

- new instance name
- platform template
- tools to include
- OS text for `exp/enving.txt`
- terminal text for `exp/enving.txt`

Non-interactive example:

```powershell
python maker.py lab_agent --platform laptop --tools shell,memadd,search,return,askuser --env-os "Windows 11 Pro" --env-terminal powershell
```

Pi/Linux-style shell tools:

```bash
python3 maker.py field_agent --platform pi --tools all --env-os "Raspberry Pi OS" --env-terminal bash
```

The maker currently discovers these built-in tool names from the selected `platforms/<platform>/tools` folder:

- `shell`
- `memadd`
- `search`
- `return`
- `askuser`

Generated layout:

```text
lab_agent/
  __init__.py
  run_lab_agent.py
  exp/
    td.txt
    enving.txt
    tool_import.txt
  tools/
    __init__.py
    ...
```

Use `--force` to replace an existing generated instance directory with the same name.

If `--env-os` or `--env-terminal` are omitted in a real interactive terminal, maker asks for them and shows detected defaults. If maker is run from a non-interactive pipe, it falls back to detected environment values.

## Watchdog Execution Flow

Watchdog follows this loop:

1. Load `.env`, tool documentation, environment documentation, and dynamic tool modules.
2. Initialize the Gemini client.
3. Load the local experience database.
4. Ask the model for a high-level plan with 3 to 7 steps.
5. Work through the plan one step at a time.
6. Ask the model for exactly one next action.
7. Execute that action through a tool module.
8. Ask the model to evaluate the last action.
9. If evaluation fails, classify the failure and run recovery.
10. If recovery later succeeds, write a new experience entry.
11. Stop when all steps finish or a hard limit is reached.

The agent is intentionally bounded. It does not run forever: every goal has action, retry, recovery, and approximate token limits.

## Tool Action Format

The model is expected to choose actions as plain strings with a prefix:

| Prefix | Meaning |
| --- | --- |
| `shell:COMMAND` | Execute a shell command. |
| `search:QUERY_OR_URL` | Search the web or extract text from a URL. |
| `memadd:TEXT` | Add text to temporary memory. |
| `memadd:KEY=VALUE` | Store a key/value in `MEMORYVALS`. |
| `askuser:QUESTION` | Ask the user for input. |
| `return:TEXT` | Append text to final returned output. |

The runners dynamically import tools by reading `exp/td.txt`. The parser extracts tool names from each line's `Command - <tool>:...` section. `return` is mapped to the module name `return_value`.

## Main Runner Modules

### `laptop/run_laptop.py`

Windows Watchdog runner. It sets `NAME = "laptop"` and imports tools from `laptop.tools`.

Responsibilities:

- Loads environment values with `python-dotenv`.
- Builds `ABS_PATH` as `os.path.join(os.getenv("ABS_PATH"), "laptop")`.
- Reads `laptop/exp/td.txt` and `laptop/exp/enving.txt`.
- Dynamically imports tool modules.
- Creates and owns the Gemini client.
- Builds strict JSON prompts for planning, action choice, action evaluation, and recovery.
- Tracks temporary memory, state, action history, completed steps, pending failures, and token estimates.
- Stores and retrieves failure/recovery examples through `ExpStore`.
- Runs a hard-bounded action loop for each plan step.

Notable current details:

- The script prints raw model output for debugging.
- Token accounting is approximate: `len(prompt) // 4 + MAX_OUTPUT_TOKENS`.
- The main block contains a hard-coded Windows demo task that creates and opens a text file.
- The decision prompt contains `CURRENT STEP:s`, which looks like a typo but is harmless text in the prompt.

### `pi/run_pi.py`

Linux/Pi Watchdog runner. It sets `NAME = "pi"` and imports tools from `pi.tools`.

It has the same core responsibilities as `laptop/run_laptop.py`, with these differences:

- Reads from `pi/exp/td.txt` and `pi/exp/enving.txt`.
- Imports tools from `pi.tools`.
- Includes `MEMORY VALUES` in the system prompt when `MEMORY_VALS` is populated.
- The main block contains a hard-coded demo goal: `use askuser.`
- The shell tool is Linux-specific and supports sudo handling.

## Shared Runner Functions

Both runners define the same major functions and classes:

| Name | Purpose |
| --- | --- |
| `tool_import()` | Reads `exp/td.txt`, extracts tool prefixes, imports matching modules, and returns callable tools. |
| `load_tool_docs()` | Loads tool documentation text for prompt context. |
| `load_env()` | Loads environment description text for prompt context. |
| `count_tokens()` | Estimates tokens by character length. |
| `safe_json()` | Serializes objects as formatted JSON for prompts. |
| `extract_json()` | Parses model output as JSON, with fallback extraction from surrounding text. |
| `_tokenize()` | Tokenizes text for sparse similarity. |
| `_vectorize()` | Builds sparse token-count vectors. |
| `_cosine_sparse()` | Computes cosine similarity between sparse vectors. |
| `ExpStore` | Loads, appends, and searches JSONL experience entries. |
| `infer_failure_name()` | Maps errors into stable failure categories. |
| `init_client()` | Creates a Google GenAI client. |
| `trim_history()` | Keeps only recent action history for prompts. |
| `build_system_prompt()` | Creates the main prompt context and execution rules. |
| `call_llm()` | Calls Gemini with retry handling and JSON parsing. |
| `create_plan()` | Requests a 3 to 7 step plan. |
| `decide_next_action()` | Requests exactly one next action for the current step. |
| `evaluate_action()` | Evaluates the last action against the current step. |
| `recover_step()` | Asks the model how to recover from a failure. |
| `run_tool()` | Dispatches `return:` internally or calls a dynamically imported tool. |
| `build_pending_failure()` | Creates a structured failure record. |
| `finalize_experience_if_needed()` | Writes a useful recovery to the experience DB after success. |
| `recover_from_failure()` | Combines failure classification, similar failure lookup, and recovery prompting. |
| `apply_recovery_decision()` | Applies retry, step replacement, skip, or abort decisions. |
| `run_agent()` | Main execution loop. |

## Experience Store

`ExpStore` stores failure/recovery examples as JSONL entries. Each entry contains:

```json
{
  "name": "shell_command_failed",
  "reason": "why it failed",
  "alternative": "shell:alternative command",
  "step": "the current plan step",
  "failed_action": "the action that failed",
  "successful_action": "the later action that worked",
  "created_at": 1710000000.0
}
```

Similarity is local and simple: text from `name`, `reason`, `step`, and `failed_action` is tokenized into sparse vectors, then compared with cosine similarity. The recovery prompt receives the top matching entries.

## Failure Categories

`infer_failure_name()` can classify failures as:

- `permission_denied`
- `repository_not_found`
- `module_not_found`
- `path_not_found`
- `tool_timeout`
- `rate_limited`
- `json_parse_failed`
- `dependency_install_failed`
- `shell_command_failed`
- `search_failed`
- `memory_write_failed`
- `generic_step_failure`

## Tool Modules

The central template copies live under `platforms/laptop/tools` and `platforms/pi/tools`. The existing `laptop/tools` and `pi/tools` folders are the tools used by those two checked-in instances.

### `platforms/laptop/tools/shell.py` and `laptop/tools/shell.py`

Persistent PowerShell wrapper for Windows.

Key parts:

- `_start_persistent_powershell()` starts `powershell -NoLogo -NoProfile -NoExit -Command -`.
- It initializes console input/output encoding to UTF-8.
- `run_shell(command)` base64-encodes the command, decodes it inside PowerShell, runs it with `Invoke-Expression`, and waits for a unique completion marker.
- `shell(command, memory, local_state)` executes a command, updates `CURRENT_WORKING_DIRECTORY` by running `pwd`, stores `last_tool_output`, and returns the standard tool result dict.
- `shell_reset()` exits and terminates the persistent PowerShell process.

Returned shape:

```python
{
    "ok": True,
    "output": output,
    "memory": memory,
    "state": local_state,
}
```

### `platforms/pi/tools/shell.py` and `pi/tools/shell.py`

Persistent Bash wrapper for Linux/Pi.

Key parts:

- Starts an interactive `/bin/bash --noprofile --norc -i` through `pty.fork()`.
- Disables prompt and command echoing.
- Maintains shell process state across commands.
- Supports sudo commands with either non-interactive `sudo -n` or password-backed `sudo -S`.
- Loads `SUDO_PASSWORD` or `TEST_SUDO_PASSWORD` from environment or `.env`.
- Sanitizes ANSI sequences, sudo prompts, echoed passwords, and common command noise.
- Applies command-output truncation policies for verbose commands like `nmap`, `apt`, and `apt-get`.
- Updates `CURRENT_WORKING_DIRECTORY` by running `pwd`.
- `shell_reset()` kills and cleans up the persistent shell process.

Important: storing sudo passwords in `.env` is convenient but sensitive. Prefer passwordless sudo for narrowly scoped commands or another safer secret-management approach.

### `platforms/*/tools/search.py`, `laptop/tools/search.py`, and `pi/tools/search.py`

Search and URL extraction tool using `ddgs`. The laptop and Pi versions are mirrored.

Functions:

- `_coerce_input(payload)` accepts a string, `{"search": "..."}`, or interactive input.
- `_is_url(value)` detects `http` and `https` URLs.
- `run_search(payload, max_results=7, max_chars=3000, region="us-en")` either extracts URL text or performs Bing-backed text search through `ddgs`.
- `search(command, memory, local_state)` wraps `run_search()` in the standard tool result shape.

Modes:

- Query mode returns normalized results with `rank`, `title`, `url`, and `snippet`.
- URL mode returns extracted page text, truncated to `max_chars`.

If a search receives a specific DDGS no-results error, the wrapper retries up to 5 times with 5-second sleeps. It also prints search output to stdout before returning.

### `platforms/*/tools/memadd.py`, `laptop/tools/memadd.py`, and `pi/tools/memadd.py`

Temporary memory tool.

Behavior:

- Copies `local_state`.
- If the last non-empty memory line already equals the command, it returns `MEMORY_ALREADY_ENDED_WITH_SAME_command`.
- If the command contains `=`, it splits on the first `=` and writes `local_state["MEMORYVALS"][key] = val`.
- Otherwise, it appends the command to the plain memory string.
- Updates `last_tool_output`.

The memory string is session-local and is not persisted to disk. `MEMORYVALS` lives in runtime state.

### `platforms/laptop/tools/askuser.py` and `laptop/tools/askuser.py`

Interactive user-input tool for the laptop runner.

Behavior:

- Calls `input(command)`.
- Stores the answer in `last_tool_output`.
- Returns the answer as `output`.

### `platforms/pi/tools/askuser.py` and `pi/tools/askuser.py`

Experimental raw-character input tool.

Behavior:

- Prints `READING RAW CHARACTERS`.
- Reads one character at a time from `sys.stdin`.
- Prints each character representation.

Current limitation: it never returns a standard tool result and loops forever unless interrupted. It is not yet compatible with the runner's expected tool contract.

### `platforms/*/tools/return_value.py`, `laptop/tools/return_value.py`, and `pi/tools/return_value.py`

Simple helper module:

```python
def return_value(aha):
    return aha
```

The runners do not normally dispatch `return:` through this module. They handle `return:` directly inside `run_tool()` by appending text to the global `returned_output`.

## `exp` Files

### `laptop/exp/td.txt` and `pi/exp/td.txt`

Tool documentation injected into the system prompt. These files also indirectly control dynamic imports because `tool_import()` parses command prefixes from them.

Current documented tools:

- Shell
- Memory
- Search
- Return
- AskUser

The parser assumes each line contains a comma-separated third field like:

```text
Command - shell:COMMAND
```

If this format changes, dynamic tool import may break.

### `laptop/exp/enving.txt`

Current laptop environment description:

```text
OS: Microsoft Windows 10 Pro
TERMINAL: powershell
```

### `pi/exp/enving.txt`

Current Pi/Linux environment description:

```text
OS: Kali GNU/Linux Rolling
TERMINAL: zsh
```

### `pi/exp/tool_import.txt`

Contains:

```text
shell search
```

Current runners do not read this file. Tool importing is based on `exp/td.txt`.

### `exp/exp.jsonl`

This file may be created at runtime by `ExpStore`. It is not present until the agent writes experience entries.

## Test / Demo Scripts

### `agent_test_makers/file_sort_test.py`

Creates a `test/` folder with sample files:

- `readme.txt`
- `data.json`
- `script.py`
- `notes.md`
- `config.ini`
- `index.html`

Then imports `run_agent` from `laptop.run_laptop` and asks the agent to sort those files into subfolders by type.

Run on Windows:

```powershell
python agent_test_makers\file_sort_test.py
```

### `file_sort_test copy.py`

Same concept as the laptop test script, but imports `run_agent` from `pi.run_pi`.

Run on Linux/Pi:

```bash
python3 "file_sort_test copy.py"
```

## Setup Scripts

### `setup.ps1`

PowerShell setup script.

It:

- Verifies Python and pip.
- Optionally creates `.venv`.
- Installs `requirements.txt`.
- Prompts for `API_KEY`.
- Writes or updates `.env` with `API_KEY` and `ABS_PATH`.
- Asks whether the current device is `pi` or `laptop`.
- Detects current OS and terminal.
- Prompts for the other device's OS and terminal.
- Writes environment descriptions to `pi/exp/enving.txt` and `laptop/exp/enving.txt`.

### `setup.sh`

Unix setup script.

It:

- Verifies `python3` and pip.
- Optionally creates `.venv`.
- Installs `requirements.txt`.
- Prompts for `API_KEY`.
- Writes or updates `.env` with `API_KEY` and `ABS_PATH`.
- Optionally stores `SUDO_PASSWORD`.
- Sets `.env` permissions to `600`.
- Asks whether the current device is `pi` or `laptop`.
- Detects OS and terminal.
- Prompts for the other device's OS and terminal.
- Writes environment descriptions to `pi/exp/enving.txt` and `laptop/exp/enving.txt`.

## Known Rough Edges

- The existing code contains mojibake in debug strings and the old README had encoding damage.
- `laptop/run_laptop.py` and `pi/run_pi.py` are mostly duplicated instead of sharing a common core.
- Both runners require `ABS_PATH`; if it is missing, `os.path.join(os.getenv("ABS_PATH"), NAME)` will fail.
- `tool_import()` error text mentions `tool_import.txt`, but the actual parser reads `exp/td.txt`.
- `return_value.py` does not match the standard tool-call signature and is usually bypassed by `run_tool()`.
- `pi/tools/askuser.py` does not return and can block forever.
- Search requires network access and may be rate limited.
- Shell tools are powerful and can modify the host system.

## Roadmap Ideas

Near-term cleanup:

- Extract shared Watchdog logic into one reusable core module.
- Keep only platform-specific shell behavior in platform packages.
- Fix setup paths to write into `pi/exp` and `laptop/exp`.
- Make all tools follow the same `(command, memory, local_state) -> dict` contract.
- Replace hard-coded demo goals with CLI arguments.
- Add tests for JSON extraction, experience retrieval, tool import parsing, and memory behavior.
- Add safer shell command policies before using the agent on important machines.

Longer-term architecture from the original project direction:

- Watchdog: bounded execution agent.
- Cleaner: maintenance for the experience database.
- Cogmet: post-task evaluation and health scoring.
- Bossman: external failsafe and rollback layer.
- Mutormentor: controlled prompt/config mutation and evaluation.

## Safety Notes

Pigion can execute shell commands chosen by an LLM. Run it only in an environment where that is acceptable. Prefer a dedicated machine, VM, or container. Keep backups of important files. Avoid giving broad sudo access unless the machine is intentionally dedicated to this agent.
