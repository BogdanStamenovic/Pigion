# Pigion Framework Wrapper Contract

Pigion now treats `platforms/` as a framework registry. The built-in framework lives at:

```text
platforms/pigion/
  core/whatchdog.py
  linux/
    exp/td.txt
    tools/
  windows/
    exp/td.txt
    tools/
```

The server registration flow creates an installed watchdog package from one framework/runtime pair. The generated `client.py` stays the same: it heartbeats, polls for jobs, imports the configured `RUNNER_MODULE`, calls `run_agent(goal)` or `run_<device>(goal)`, then posts the result back to the web server.

## Required Layout

Every framework wrapper must use this shape:

```text
platforms/<framework>/
  core/whatchdog.py
  <runtime>/
    framework.json
    exp/td.txt
    tools/
      __init__.py
      shell.py
      return_value.py
      ...
```

`<framework>` and `<runtime>` must be valid lowercase package-style names: letters, numbers, and underscores, starting with a letter or underscore. Examples:

```text
platforms/pigion/linux
platforms/pigion/windows
platforms/langchain/linux
platforms/autogen/linux
platforms/gpt_researcher/windows
```

## Core Runner Contract

`platforms/<framework>/core/whatchdog.py` is copied into the generated device package as `run_<device>.py`.

It must expose one of these callables:

```python
def run_agent(goal: str):
    ...
```

or:

```python
def run_<device>(goal: str):
    ...
```

The generated `client.py` will call that function with the queued goal text. The return value must be JSON-serializable or at least safely stringifiable. A good return value is:

```python
{
    "ok": True,
    "output": "Finished the requested task.",
}
```

On failure, raise an exception. The client catches it and posts the traceback to the server job record.

The server generator replaces these placeholders in the copied runner:

```python
NAME = "pi"
NAME = "laptop"
run_pi
run_laptop
```

If your wrapper does not use those exact strings, make sure it still exposes `run_agent(goal)` so `client.py` can call it without name rewriting.

## Runtime Tool Contract

Each runtime directory owns the tools for that OS/runtime target:

```text
platforms/<framework>/<runtime>/tools/<tool>.py
```

Tool modules should expose a callable named like the command in `td.txt`. The current Pigion runner expects:

```python
def tool_name(command, memory, local_state, program_state=None):
    return {
        "ok": True,
        "output": "Human-readable result",
        "memory": memory,
        "state": local_state,
        "program_state": program_state or {},
    }
```

For the special `return` command, the Python file must be `return_value.py` and expose `return_value(...)`.

Other frameworks may adapt internally, but the copied runner must still finish through `run_agent(goal)` and return a final result to `client.py`.

## Framework Questions Contract

Every runtime must provide a versioned manifest:

```text
platforms/<framework>/<runtime>/framework.json
```

The manifest is the source of truth for registration, tools, supported operating systems, and installation. Invalid manifests are excluded from the registration selector and reported in its diagnostics.

```json
{
  "schema_version": 1,
  "framework": "example",
  "runtime": "linux",
  "display_name": "Example Framework",
  "description": "Short capability summary.",
  "supported_os": ["linux"],
  "runner": "../core/whatchdog.py",
  "allow_zero_tools": false,
  "uses_pigion_model_config": false,
  "tools": [
    {
      "name": "return",
      "module": "return_value.py",
      "policy": "required",
      "documentation": "Return, Description: Finishes the goal, Command - return:TEXT, Example - return:Done"
    },
    {
      "name": "search",
      "module": "search.py",
      "policy": "optional",
      "documentation": "Search, Description: Searches the web, Command - search:QUERY, Example - search:Pigion"
    }
  ],
  "questions": [
    {
      "name": "FRAMEWORK_ENV_KEY",
      "label": "Question shown to the user",
      "default": "default value",
      "type": "text",
      "required": true,
      "secret": false,
      "options": ["optional", "select", "values"],
      "help": "Optional extra context."
    }
  ],
  "lifecycle": {
    "install": "lifecycle/install.sh",
    "verify": "lifecycle/verify.sh",
    "upgrade": "lifecycle/upgrade.sh",
    "uninstall": "lifecycle/uninstall.sh"
  }
}
```

Tool policy has three values:

- `required`: selected and locked in the web UI; automatically restored during server-side validation.
- `default`: initially selected but the user may remove it.
- `optional`: initially unselected and available to add.

When `--tools` is omitted, `maker.py` selects required and default tools. Explicit CLI and web selections are validated against the same manifest. Required tools are always included. `allow_zero_tools` controls whether an empty final selection is valid.

Set `uses_pigion_model_config` to `true` only when the wrapper consumes the built-in Gemini/OpenAI/Ollama fields. Other frameworks should declare their own provider questions and the web UI will hide the Pigion-specific model controls.

`name` must be a valid environment-variable style identifier. The web server writes the selected answers into the generated installer `.env`; `maker.py` writes them to `<device>/exp/framework_env.txt` for locally generated packages. Framework runners should load those values themselves and translate them into whatever their upstream library expects.

Question `type` may be `text`, `password`, `select`, or `url`. A question may also provide `options`, `default`, `required`, `secret`, and `help`. Secret defaults are never returned by the registration-spec API.

CLI generation can provide answers non-interactively:

```text
python3 -m maker researcher --framework gpt_researcher --platform linux --framework-env GPT_RESEARCHER_LLM_MODEL=llama3.1
```

## Installation Lifecycle Contract

Lifecycle scripts are trusted repository code, but paths must remain inside their runtime directory. Absolute paths, `..` traversal outside the runtime, missing scripts, unknown lifecycle phases, and unsupported manifests are rejected before registration. Supported phases are `install`, `verify`, `upgrade`, and `uninstall`.

The generated Linux or Windows installer creates the shared Pigion venv and `.env` first, then runs `install` and `verify` before enabling the watchdog service. A non-zero exit aborts installation, so scripts must print an actionable error without printing credentials. `install`, `verify`, and `upgrade` must be safe to run repeatedly. Framework `uninstall` runs before the shared service and file cleanup when supplied.

Lifecycle scripts receive framework answers from `.env` plus:

```text
PIGION_INSTALL_DIR
PIGION_VENV_PYTHON
PIGION_DEVICE_NAME
PIGION_DEVICE_UUID
PIGION_SERVER_URL
PIGION_SELECTED_TOOLS
PIGION_FRAMEWORK
PIGION_RUNTIME
PIGION_SERVICE_USER       # Linux
PIGION_SERVICE_HOME       # Linux
```

Linux lifecycle files use Bash; Windows lifecycle files use PowerShell. The manifest may point to arbitrary scripts, but registration users cannot provide or override those paths.

## Tool Documentation Contract

Each runtime should provide:

```text
platforms/<framework>/<runtime>/exp/td.txt
```

Each tool line must contain a `Command - name:` segment, because the Pigion runner uses it to import tool modules.

Example:

```text
1.Shell, Description: Executes a shell command, Command - shell:COMMAND, Example - shell:pwd
2.Return, Description: Sends a final answer, Command - return:TEXT, Example - return:Done
```

`td.txt` remains a generated runner input. The manifest tool list and its inline documentation are authoritative; only selected tools are copied and written to generated `td.txt` and `tool_import.txt`.

## What The Server Provides

During registration, the server:

1. Selects `framework/runtime`, for example `pigion/linux`.
2. Validates the runtime manifest and the submitted required/default/optional tool selection.
3. Copies `platforms/<framework>/core/whatchdog.py` into `<device>/run_<device>.py`.
4. Copies the discovered tool modules from `platforms/<framework>/<runtime>/tools/`.
5. Writes `<device>/exp/td.txt`, `<device>/exp/enving.txt`, and `<device>/exp/tool_import.txt`.
6. Bundles lifecycle scripts and generates an installer that downloads the bundle and the unchanged `client.py`.
7. Stores `framework`, `platform`, manifest schema/hash, lifecycle identifiers, runner module, and selected tools in `server/devices.json`.

The communication layer does not know which framework is behind the runner. It only knows the device UUID, server URL, and runner module.

## Wrapper Checklist

Before adding a new framework wrapper, make sure:

- `platforms/<framework>/core/whatchdog.py` imports without hidden local-only paths.
- The copied runner exposes `run_agent(goal)` or a rewritten `run_<device>(goal)`.
- Every documented command has a matching tool file in `tools/`.
- `td.txt` command names match the callable/import names expected by the runner.
- The final runner return value is JSON-serializable or stringifiable.
- Runtime dependencies are listed in the repo `requirements.txt` or installed by the wrapper at runtime.
- `python -m py_compile maker.py server/server.py platforms/<framework>/core/whatchdog.py` passes.

## GPT Researcher Wrapper

The `gpt_researcher/linux` and `gpt_researcher/windows` wrappers are research framework options. Their intended stack is:

- LLM: the model chosen at registration.
- Inference: Ollama.
- Embeddings: `nomic-embed-text` through Ollama.
- Search: SearXNG/Searx via GPT Researcher's `RETRIEVER=searx`.
- Browser: Playwright.
- Extraction: Trafilatura by default, or Crawl4AI when selected.
- Vector DB: Qdrant when `GPT_RESEARCHER_QDRANT_URL` is set.

The framework install lifecycle installs Python dependencies and the Playwright browser before the watchdog service starts. On Linux it also pulls local Ollama models when the configured Ollama URL points to localhost and the CLI is available. The runner performs no package or browser installation. Set this for a runner-only configuration check:

```text
PIGION_GPT_RESEARCHER_DRY_RUN=1
```

For Windows registrations, the server generates a PowerShell installer at `/install/<uuid>.ps1` plus a zip bundle. The installer creates a venv under `%LOCALAPPDATA%\\Pigion\\DEVICE_NAME` by default, writes `.env`, creates `watchdog.ps1` and `uninstall.ps1`, registers a user scheduled task, and starts it immediately. The generated client launches `uninstall.ps1` for remote removal jobs. Windows GPT Researcher devices can use a remote Ollama/SearXNG/Qdrant server through the registration URLs; the Pigion webserver only writes configuration and does not serve embeddings or LLM inference.
