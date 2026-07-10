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

A runtime may ask registration-time questions by adding:

```text
platforms/<framework>/<runtime>/framework.json
```

The file is optional. When present, `maker.py` and the web registration page read a top-level `questions` list:

```json
{
  "description": "Short capability summary.",
  "questions": [
    {
      "name": "FRAMEWORK_ENV_KEY",
      "label": "Question shown to the user",
      "default": "default value",
      "required": true,
      "options": ["optional", "select", "values"],
      "help": "Optional extra context."
    }
  ]
}
```

`name` must be a valid environment-variable style identifier. The web server writes the selected answers into the generated installer `.env`; `maker.py` writes them to `<device>/exp/framework_env.txt` for locally generated packages. Framework runners should load those values themselves and translate them into whatever their upstream library expects.

CLI generation can provide answers non-interactively:

```text
python3 -m maker researcher --framework gpt_researcher --platform linux --framework-env GPT_RESEARCHER_LLM_MODEL=llama3.1
```

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

The web registration page reads this file automatically. Tools listed here are selected without manual checkboxes, in the same order they appear in `td.txt`. If the runtime has additional Python tool files that are not listed in `td.txt`, `maker.py` appends them after the documented tools and generates fallback doc lines.

## What The Server Provides

During registration, the server:

1. Selects `framework/runtime`, for example `pigion/linux`.
2. Discovers tools from `platforms/<framework>/<runtime>/exp/td.txt` and then from matching files in `tools/`.
3. Copies `platforms/<framework>/core/whatchdog.py` into `<device>/run_<device>.py`.
4. Copies the discovered tool modules from `platforms/<framework>/<runtime>/tools/`.
5. Writes `<device>/exp/td.txt`, `<device>/exp/enving.txt`, and `<device>/exp/tool_import.txt`.
6. Generates an installer that downloads the bundle and the unchanged `client.py`.
7. Stores `framework`, `platform`, `runner_module`, and discovered tools in `server/devices.json`.

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

The `gpt_researcher/linux` wrapper is a research framework option. Its intended stack is:

- LLM: the model chosen at registration.
- Inference: Ollama.
- Embeddings: `nomic-embed-text` through Ollama.
- Search: SearXNG/Searx via GPT Researcher's `RETRIEVER=searx`.
- Browser: Playwright.
- Extraction: Trafilatura by default, or Crawl4AI when selected.
- Vector DB: Qdrant when `GPT_RESEARCHER_QDRANT_URL` is set.

The wrapper auto-installs missing Python packages inside the generated watchdog venv on first run, installs the Playwright browser, and best-effort pulls the configured Ollama LLM and embedding model when the `ollama` CLI is available. Set these for verification-only runs:

```text
PIGION_GPT_RESEARCHER_DRY_RUN=1
PIGION_GPT_RESEARCHER_SKIP_DEP_INSTALL=1
PIGION_GPT_RESEARCHER_SKIP_OLLAMA_PULL=1
```
