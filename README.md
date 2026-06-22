# Pigion

Pigion is an experimental local autonomous-agent framework. The active runner in this workspace is `pi/run_pi.py`, a Linux/Pi-oriented loop that uses Gemini for goal formalization, planning, action selection, action evaluation, interactive-session evaluation, and failure recovery. Tool calls are executed through Python modules in `pi/tools`.

The current runner is intentionally bounded: it creates a plan, works one plan step at a time, executes one tool action at a time, evaluates progress, attempts recovery on failures, and stops when the plan is complete or a configured limit is reached.

## Current Layout

```text
Pigion/
  README.md
  TOOLDOCS.md
  requirements.txt
  setup.sh
  setup.ps1
  maker.py
  file_sort_test copy.py
  test.py
  agent_test_makers/
  laptop/
    run_laptop.py
    exp/
    tools/
  pi/
    run_pi.py
    exp/
      td.txt
      enving.txt
      exp.jsonl
    tools/
      askuser.py
      memadd.py
      return_value.py
      search.py
      shell.py
  pi_exp/
    exp.jsonl
  platforms/
    core/
      whatchdog.py
    pi/
    windows/
```

`pi/` is the active runtime and tool package. `laptop/` and `platforms/` are older/generated platform snapshots and reference implementations; they are useful for comparison, but `pi/run_pi.py` is the current development target.

## Requirements

Runtime dependencies are in `requirements.txt`:

- `google-genai` for Gemini calls.
- `python-dotenv` for `.env` loading.
- `ddgs` for search and URL extraction.
- `protobuf`, used by the Google client stack.

Python 3 is required.

## Setup

Linux/Pi:

```bash
chmod +x setup.sh
./setup.sh
```

Windows PowerShell:

```powershell
.\setup.ps1
```

The setup scripts can create `.venv`, install requirements, write `.env`, and populate `pi/exp/enving.txt` and `laptop/exp/enving.txt`.

At minimum, `.env` needs:

```env
API_KEY="your-google-genai-key"
ABS_PATH="/absolute/path/to/Pigion"
```

Optional runtime settings:

```env
SUDO_PASSWORD=""
TEST_SUDO_PASSWORD=""
GEMINI_MODEL="gemini-2.5-flash-lite"
LLM_TEMPERATURE="0.3"
```

## Running

The Pi runner still has a hard-coded demo goal at the bottom of `pi/run_pi.py`:

```bash
python3 pi/run_pi.py
```

To run your own goal from Python:

```python
from pi.run_pi import run_agent

run_agent("Inspect the current directory and summarize the files.")
```

## Runner Flow

`pi/run_pi.py` currently follows this flow:

1. Load `.env`, `pi/exp/td.txt`, and `pi/exp/enving.txt`.
2. Dynamically import tools described in `pi/exp/td.txt`.
3. Optionally formalize the user goal.
4. Create a 3 to 7 step high-level plan.
5. Work through the plan one step at a time.
6. Ask the model for exactly one next action.
7. Dispatch the action to a tool through `run_tool()`.
8. Evaluate the action for the current step.
9. If a tool enters interactive mode, run `start_interactive_mode()` and then evaluate all plan steps with `evaluate_action_interactive()`.
10. On failure, classify the error, search similar past failures in `pi/exp/exp.jsonl`, and ask for a recovery decision.
11. Continue until all steps are done or a configured limit is reached.

Most model calls share `build_system_prompt()` plus role-specific task prompts. Interactive evaluation uses a narrower evaluator system prompt that checks the status of every plan step.

## Important Runtime State

`agent_state` is model-visible runtime state. It includes values such as:

- `memory`
- `MEMORYVALS`
- `last_action`
- `last_tool_output`
- `pending_failure`
- `last_similar_failures`
- `CURRENT_WORKING_DIRECTORY`

`program_state` is internal loop/tool metadata. It is passed to tools but is not included in the normal state block shown to the agent. Current uses include:

- `force_next_action`
- `exp_cache_loaded`
- `original_goal`
- `formalized_goal`
- `INTERACTIVE_MODE`
- `INTERACTIVE_COMPLETED`
- `BRANCH_MODE`
- `ACTIVE_SESSION`
- `SESSION_LABEL`
- `BRANCH_WORKING_DIRECTORY`
- `plan_status`

The interactive plan status map is intentionally kept in `program_state` so the loop can use it without showing raw bookkeeping back to the agent.

## Tool Actions

The model chooses actions as strings:

| Action | Meaning |
| --- | --- |
| `shell:COMMAND` | Run a shell command through `pi/tools/shell.py`. |
| `search:QUERY_OR_URL` | Search the web or extract text from a URL. |
| `memadd:TEXT` | Append temporary memory. |
| `memadd:KEY=VALUE` | Store a runtime key/value in `MEMORYVALS`. |
| `askuser:QUESTION` | Ask the local user a question. This is handled directly by `run_tool()` with `input()`. |
| `return:TEXT` | Append text to final returned output. This is handled directly by `run_tool()`. |

There is no source `memget` tool in the current `pi/tools` directory, so stored `MEMORYVALS` are visible only through runtime state unless a future tool or dispatcher feature adds retrieval/substitution.

Tool documentation lives in `pi/exp/td.txt`. `tool_import()` parses that file and imports the command prefixes it finds in the `Command - prefix:...` field. `return` maps to the module name `return_value` during import, although `return:TEXT` is normally handled directly before dynamic dispatch.

See `TOOLDOCS.md` for the tool contract and how to add new tools.

## Current Pi Tools

### `shell`

`pi/tools/shell.py` is the main execution tool. It maintains a persistent Bash PTY for regular commands and a separate branch PTY for interactive programs such as `ssh`, `sftp`, shells, REPLs, editors, and terminal programs.

Highlights:

- Starts `/bin/bash --noprofile --norc -i` through `pty.fork()`.
- Disables prompt and echo noise where possible.
- Tracks `CURRENT_WORKING_DIRECTORY`.
- Supports sudo through `SUDO_PASSWORD` or `TEST_SUDO_PASSWORD`.
- Truncates or filters verbose output for commands such as `apt` and `nmap`.
- Detects interactive prompts and returns `interactive_mode: True`.
- Uses branch session labels such as `sftp_interactive` or `@host`.
- Keeps branch labels stable while interactive input is being sent.

### `search`

`pi/tools/search.py` uses `ddgs` to search text queries or extract page text from URLs. It returns a stringified result object and updates `last_tool_output`.

Search requires network access and can be rate-limited. One successful retry path currently omits `program_state` from its return object.

### `memadd`

`pi/tools/memadd.py` stores temporary context.

- Plain text is appended to the in-memory `memory` string.
- `KEY=VALUE` writes into `local_state["MEMORYVALS"]`.
- Duplicate last-line memory writes are ignored.

Memory is runtime-local unless another caller persists it.

### `askuser`

`askuser:TEXT` is handled directly in `run_tool()` with `input()`. The separate `pi/tools/askuser.py` file is not compatible with the current standard tool contract and should be treated as stale until rewritten.

### `return`

`return:TEXT` is handled directly in `run_tool()`, not normally by `pi/tools/return_value.py`. It appends `TEXT` to the global `returned_output`.

## Experience Store

`ExpStore` stores failure and recovery examples in JSONL. Each entry looks like:

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

Similarity is local and simple: fields are tokenized into sparse vectors and compared with cosine similarity. Recovery receives the closest matches.

## Key Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEY` | none | Google GenAI API key. |
| `ABS_PATH` | required | Project root. The runner appends `pi`. |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Model used for runner prompts. |
| `LLM_TEMPERATURE` | `0.3` | Generation temperature. |
| `MAX_OUTPUT_TOKENS` | `700` | Max model output tokens per call. |
| `MAX_ACTIONS_PER_STEP` | `12` | Action rounds per plan step. |
| `MAX_LLM_RETRIES` | `6` | Model-call retry limit. |
| `MAX_RECOVERY_ATTEMPTS` | `6` | Recovery retry/replacement limit. |
| `TOKENS_PER_GOAL` | `100000` | Approximate token budget per goal. |
| `EXP_DB_PATH` | `<ABS_PATH>/exp/exp.jsonl` | Experience DB path after `ABS_PATH` is expanded to `<project>/pi`. |
| `SIMILAR_FAILURES_TOP_K` | `5` | Similar failures passed to recovery. |
| `USE_GOAL_FORMALIZER` | `True` | Whether to rewrite goals before planning. |
| `MAX_SHELL_OUTPUT` | `200000` | Shell output size before truncation. |
| `SHELL_TAIL_LINES` | `50` | Fallback tail line count. |
| `SHELL_TRUNCATE_MIN_LINES` | `20` | Minimum line count before truncation. |
| `SHELL_INTERACTIVE_IDLE_SECONDS` | `30` | Idle wait for interactive branch reads. |
| `SHELL_INTERACTIVE_PROMPT_GRACE_SECONDS` | `0.5` | Prompt grace wait. |
| `SUDO_PASSWORD` | none | Optional sudo password. |
| `TEST_SUDO_PASSWORD` | none | Alternate sudo password variable. |

## TODO

- Implement ShadowFS in the core Pigion runtime so agent file mutations can be staged, inspected, committed, or discarded instead of always touching the host filesystem directly.
- Move shared runtime behavior out of `pi/run_pi.py`, `laptop/run_laptop.py`, and `platforms/core/whatchdog.py` into a real core module.
- Replace the hard-coded demo goal with a small CLI or function-first entrypoint.
- Rewrite `pi/tools/askuser.py` to match the current tool contract or remove it from dynamic import.
- Decide whether `MEMORYVALS` should get an explicit retrieval/substitution tool and document that behavior in `pi/exp/td.txt`.
- Fix the `search` retry success return path so it includes `program_state`.
- Make `tool_import()` parse `pi/exp/td.txt` with a less brittle format.

## Known Rough Edges

- `pi/run_pi.py` and `laptop/run_laptop.py` are duplicated instead of sharing a core.
- `platforms/core/whatchdog.py` is a copied runtime snapshot, not a clean reusable core.
- `ABS_PATH` is required; missing it will break path construction because `pi/run_pi.py` immediately appends `pi`.
- `tool_import()` error text mentions `tool_import.txt`, but current imports are based on `exp/td.txt`.
- `tool_import()` assumes the command field is the third comma-separated field in each `td.txt` line.
- `pi/tools/askuser.py` does not satisfy the current standard tool contract, even though `askuser:` actions work through direct `run_tool()` handling.
- `pi/tools/search.py` has one retry path that can return without `program_state` on success.
- The runner prints raw model output and internal state for debugging.
- The main runner still uses a hard-coded demo goal.
- The shell tool is powerful and can modify the host system.

## Safety

Pigion can execute shell commands chosen by an LLM. Run it only on a machine, VM, or container where that is acceptable. Keep backups of important files, be careful with sudo, and treat `.env` as sensitive if it contains credentials.
