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
  server/
    server.py
    device_client.py
    web_store.py
    devices.json
    jobs.json
    config.json
    sessions.json
    installers/
  orchestrator/
    run_orchestrator.py
    exp/
      td.txt
      enving.txt
      exp.jsonl
    tools/
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
    linux/
    windows/
```

`pi/` is the active local runtime and tool package. `server/` contains the lightweight FastAPI dashboard, JSON storage, device polling API, and installers. `orchestrator/` is the orchestrator agent package; generated device tool modules are written to `orchestrator/tools/` because `orchestrator/run_orchestrator.py` imports tools from `orchestrator/exp/td.txt`.

## Requirements

Runtime dependencies are in `requirements.txt`:

- `google-genai` for Gemini calls.
- `python-dotenv` for `.env` loading.
- `ddgs` for search and URL extraction.
- `protobuf`, used by the Google client stack.
- `fastapi` for the orchestrator dashboard and device API.
- `uvicorn` for serving the orchestrator dashboard.

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

The setup scripts can create `.venv`, install requirements, write `.env`, and install the webserver/orchestrator systemd services.

At minimum, `.env` needs:

```env
ABS_PATH="/absolute/path/to/Pigion"
LLM_PROVIDER="gemini"
LLM_MODEL="gemini-2.5-flash-lite"
LLM_API_KEY="your-model-api-key"
```

Optional runtime settings:

```env
SUDO_PASSWORD=""
TEST_SUDO_PASSWORD=""
GEMINI_API_KEY="your-google-genai-key"
OPENAI_API_KEY="your-openai-key"
OPENAI_BASE_URL="https://api.openai.com/v1"
OLLAMA_HOST="http://127.0.0.1:11434"
API_KEY="legacy-google-genai-key"
GEMINI_MODEL="gemini-2.5-flash-lite"
LLM_TEMPERATURE="0.3"
PIGION_ORCHESTRATOR_USER="admin"
PIGION_ORCHESTRATOR_PASSWORD="pigion"
PIGION_SERVER_URL="http://127.0.0.1:8000"
PIGION_HEARTBEAT_TIMEOUT="60"
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

## Orchestrator Dashboard

`server/server.py` is a small FastAPI app for registering watchdog devices, queueing work, and showing device/job status. It intentionally uses JSON files only:

| File | Purpose |
| --- | --- |
| `server/devices.json` | Registered devices, UUIDs, generated command names, installer paths, and heartbeat state. |
| `server/jobs.json` | Queued, running, finished, and failed goals. |
| `server/config.json` | Login defaults, heartbeat timeout, session TTL, and public server URL. |
| `server/sessions.json` | Login session cookies. |

Start it manually with:

```bash
python -m uvicorn server.server:app --host 127.0.0.1 --port 8000
```

Or install the repo-local services:

```bash
./install.sh
```

The installer creates and enables:

- `pigion-web.service`, which runs the FastAPI webserver.
- `pigion-orchestrator.service`, which polls the webserver for goals targeting the local orchestrator and runs `orchestrator.run_orchestrator` for each queued goal.

The service binds to `0.0.0.0` so it is reachable through normal LAN interfaces and Tailscale. When Tailscale is installed, the installer prefers the machine's Tailscale IPv4 address for `PIGION_SERVER_URL`, so generated device installers point at the Tailscale address. Override this with:

```bash
PIGION_PUBLIC_HOST=100.x.y.z ./install.sh
```

Uninstall the repo-local services with:

```bash
./uninstall.sh
```

That stops/disables `pigion-web.service` and `pigion-orchestrator.service`, removes their unit files and enablement symlinks, reloads systemd, and leaves repo data such as `.env`, `.venv`, and JSON state intact.

Then open:

```text
http://<tailscale-ip-or-hostname>:8000/login
```

The default login is `admin` / `pigion`. For real use, set `PIGION_ORCHESTRATOR_USER` and `PIGION_ORCHESTRATOR_PASSWORD` before the first run, or edit `server/config.json`.

The dashboard currently includes:

- Device list with heartbeat status.
- `(DEVICE IS DOWN)` display when the last heartbeat is older than `heartbeat_timeout_seconds`.
- Queue counts for running and waiting jobs.
- Recent goals.
- Recent logs.
- Register Device page.
- Remove Registered Device page.
- Send Goal page. Choose `Local Orchestrator` to route the goal through the orchestrator, or choose a registered device to queue directly to that device.

### Device Registration

The Register Device page does the first-pass provisioning work:

1. Runs `git pull` in the project root.
2. Asks the same core questions as `maker.py`: watchdog name, framework/runtime template, OS, and terminal.
3. Discovers tools automatically from the selected framework/runtime `exp/td.txt` and tool files.
4. Prompts for model provider, model name, model API key, Ollama host, and OpenAI base URL.
5. Prompts for the target device sudo password.
6. Optionally prompts for the browser-facing `td.txt` capability block used by the orchestrator; if left blank, the server builds one from the discovered tool docs.
7. Calls `maker.create_instance()` with the selected framework/runtime, discovered tools, and entered environment text.
8. Generates a UUID for the device.
9. Stores the device in `server/devices.json`.
10. Generates install endpoints under `/install/<uuid>...`.
11. Adds a device command to `orchestrator/exp/td.txt`.
12. Generates the matching `orchestrator/tools/device_DEVICE_NAME.py` module. The agent sees the device name in the command; the generated tool keeps the UUID internally.

The generated orchestrator tool queues a goal for that device. The command name uses the registered device name, not the UUID. For example, after registering a device named `Bogdan`, the orchestrator may advertise:

```text
device_Bogdan:Check system temperature
```

Because `orchestrator/run_orchestrator.py` imports tools from `orchestrator/exp/td.txt`, the generated tool module is required. The `td.txt` entry also preserves the current brittle parser shape: the third comma-separated field must contain `Command - prefix:...`.

### Orchestrator Goals

The webserver reserves the internal job target `__orchestrator__` for local orchestrator work. The browser `Send Goal` page exposes this as `Local Orchestrator`; posted goals are picked up by `server/orchestrator_client.py`. The worker starts a fresh orchestrator process for each goal so new device registrations are visible without restarting the worker.

Goals can also be queued through JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/orchestrator/goals \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Check which registered device should handle this."}'
```

The final registration page prints an installer command:

```bash
curl -fsSL http://127.0.0.1:8000/install/<device_uuid>.sh | bash
```

Set `PIGION_SERVER_URL` to the reachable host name before registering devices if the device should install from another machine, for example:

```env
PIGION_SERVER_URL="http://100.x.y.z:8000"
```

The generated `install.sh` downloads a bundle of the generated watchdog package, downloads a per-device `client.py`, creates a virtual environment at `/opt/pigion/DEVICE_NAME/.venv`, installs dependencies inside that venv, writes `/opt/pigion/DEVICE_NAME/.env`, writes `/opt/pigion/DEVICE_NAME/uninstall.sh`, writes `/etc/systemd/system/pigion_DEVICE_NAME.service`, enables the service, and starts it. The service runs as the installing user, starts from that user's home directory, and keeps the code/config under `/opt/pigion/DEVICE_NAME` by default. Override that install location with `PIGION_INSTALL_ROOT` when running the installer.

The target `.env` is populated automatically from registration:

```env
ABS_PATH="/opt/pigion/DEVICE_NAME"
API_KEY="..."
LLM_PROVIDER="gemini"
LLM_MODEL="gemini-2.5-flash-lite"
LLM_API_KEY="..."
GEMINI_API_KEY="..."
OPENAI_API_KEY="..."
OPENAI_BASE_URL="https://api.openai.com/v1"
OLLAMA_HOST="http://127.0.0.1:11434"
SUDO_PASSWORD="..."
TEST_SUDO_PASSWORD="..."
```

For `LLM_PROVIDER=gemini`, use a Gemini model such as `gemini-2.5-flash-lite` and provide a model API key. For `LLM_PROVIDER=openai`, use an OpenAI chat model such as `gpt-4.1-mini` and provide a model API key. For `LLM_PROVIDER=ollama`, set `LLM_MODEL` to a local model name and `OLLAMA_HOST` to the reachable Ollama URL, for example `http://192.168.1.50:11434`; no model API key is required.

The sudo password is also used by the installer for its privileged setup steps. Because model credentials, sudo credentials, and generated install settings are embedded in the generated installer endpoint and stored in local JSON for now, treat `server/devices.json` and `/install/<uuid>.sh` as sensitive.

The generated device-side `client.py`:

1. Sends heartbeat updates.
2. Polls for waiting jobs.
3. Sends the goal to that device's generated watchdog runner.
4. Marks the job started/running/finished or failed.
5. Sends result/error data and logs back to the server.

### Device Removal

The dashboard has a `Remove Registered Device` action. Removing a device queues a special uninstall job for that device. The installed client handles that job by launching `/opt/pigion/DEVICE_NAME/uninstall.sh`, which reads sudo credentials from `/opt/pigion/DEVICE_NAME/.env` and removes the device service plus install directory on the target. After the device acknowledges the uninstall job, the server deletes its registry row, queued jobs, generated orchestrator tool, generated installer, and generated local package. If the device is down or does not acknowledge in time, it remains registered with the uninstall job pending so it can receive the command when it comes back online.

### Device Client API

Watchdog devices poll the server instead of using WebSockets. The current API is:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/jobs/<device_uuid>` | Return the next waiting job for a device, or `{"job": null}`. |
| `POST` | `/api/jobs/<job_id>/started` | Mark a job as running. |
| `POST` | `/api/jobs/<job_id>/progress` | Update progress text and optionally append a log line. |
| `POST` | `/api/jobs/<job_id>/finished` | Mark a job finished or failed with result/error data. |
| `POST` | `/api/device/<uuid>/heartbeat` | Refresh the device heartbeat and mark it online. |
| `POST` | `/api/device/<uuid>/logs` | Append device/job logs. |

There is also a generic polling client at `server/device_client.py` for local testing:

```bash
python server/device_client.py \
  --server http://127.0.0.1:8000 \
  --uuid <device_uuid> \
  --runner <package>.run_<package>
```

The generated installer command shown after registration runs this client with the device UUID and generated runner module. The client loop is intentionally simple:

1. Send heartbeat.
2. Poll for a waiting job.
3. If a job exists, mark it started.
4. Run the generated watchdog runner against the goal.
5. Report success, failure, current status, and logs.

### Orchestrator Scope

This first implementation deliberately does not include WebSockets, Docker, PostgreSQL, user accounts, HTTPS, OAuth, multiple orchestrators, multi-device jobs, async distributed execution, plugin systems, or auto-discovery.

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
| `ABS_PATH` | required | Project root. The runner appends `pi`. |
| `LLM_PROVIDER` | `gemini` | Model backend: `gemini`, `openai`, or `ollama`. |
| `LLM_MODEL` | provider-specific | Model used for runner prompts. Defaults to `gemini-2.5-flash-lite`, `gpt-4.1-mini`, or `llama3.1`. |
| `LLM_API_KEY` | none | Provider-neutral model API key used by Gemini/OpenAI. |
| `GEMINI_API_KEY` | none | Gemini API key. Preferred over `LLM_API_KEY` for Gemini when set. |
| `OPENAI_API_KEY` | none | OpenAI API key. Preferred over `LLM_API_KEY` for OpenAI when set. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible chat completions base URL. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL or IP/port for local models. |
| `API_KEY` | none | Legacy fallback API key, still accepted for Gemini/OpenAI compatibility. |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Legacy Gemini model fallback when `LLM_MODEL` is unset. |
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
