# Framework Integration Helper Prompt

Use this prompt when handing Pigion framework-integration work to another coding agent.

```text
You are working in /home/bogdan/Pigion_wrapper/Pigion.

Your job is to integrate a new framework as a Pigion framework wrapper. Read the live repo first, especially:

- FRAMEDOCS.md
- maker.py
- server/server.py
- server/device_client.py
- platforms/pigion/core/whatchdog.py
- platforms/gpt_researcher/core/whatchdog.py, if present

Do not change the generated client protocol unless the user explicitly asks. The generated client must remain framework-agnostic: it heartbeats, polls jobs, imports RUNNER_MODULE, calls run_agent(goal) or run_<device>(goal), and posts the returned result or exception back to the webserver.

Add the framework under:

platforms/<framework>/
  core/whatchdog.py
  <runtime>/
    framework.json
    exp/td.txt
    tools/
      __init__.py
      <tool>.py

Names must be lowercase package-style identifiers: letters, numbers, and underscores, not starting with a number.

The core runner contract:

- platforms/<framework>/core/whatchdog.py is copied into the generated device package as run_<device>.py.
- It must expose def run_agent(goal: str), or expose a run_<device>(goal) function after maker placeholder rewriting.
- The return value must be JSON-serializable or safely stringifiable.
- Raise exceptions on real failure; the client catches and posts tracebacks to server jobs.
- Do not rely on paths outside the generated package unless they are optional or configurable.

The runtime tool contract:

- platforms/<framework>/<runtime>/exp/td.txt must contain tool docs.
- Each tool line must include Command - name: so maker can discover tool names.
- Each discovered tool must have a matching tools/<name>.py file, except return maps to tools/return_value.py.
- A simple framework can expose one placeholder tool if the runner itself handles the main job through run_agent().

Framework questions:

- Add platforms/<framework>/<runtime>/framework.json when the framework needs registration-time configuration.
- Use a top-level description and questions list.
- Each question should have name, label, default, required, optional options, and optional help.
- name must be an environment-variable style key.
- The webserver writes these answers into the installed device .env.
- maker.py writes them into exp/framework_env.txt for locally generated packages.
- The wrapper runner must load these values and map them into whatever upstream env/config variables the framework expects.

Example framework.json:

{
  "description": "Short capability summary.",
  "questions": [
    {
      "name": "MY_FRAMEWORK_MODEL",
      "label": "Model",
      "default": "default-model",
      "required": true,
      "help": "Model name used by this framework."
    }
  ]
}

If the framework needs dependencies:

- Prefer installing dependencies inside the generated watchdog venv, not globally.
- If dependency installation should happen on the device, implement first-run auto-install in the wrapper.
- Add clear skip env vars for dry tests, for example PIGION_<FRAMEWORK>_SKIP_DEP_INSTALL=1.
- Keep service dependencies configurable. Do not hard-code localhost when the user may want a remote service.
- For external services, ask for URLs in framework.json and write them to .env through the web registration flow.

If the framework is GPT Researcher-like, the expected behavior is:

- Registration asks the user for LLM model, Ollama base URL, embedding model, search URL, browser/extraction choices, and optional vector DB URL.
- The installed watchdog can use Ollama from another server by configuring the Ollama base URL.
- Embeddings also go to the configured Ollama server, not the Pigion webserver.
- Pigion webserver stores and writes configuration; it does not serve LLM or embedding requests.
- The wrapper should map user answers to the upstream framework env names.
- The wrapper should dry-run without downloading packages or pulling models when skip env vars are set.

Webserver integration expectations:

- /register must list the framework/runtime from discover_frameworks() and discover_platforms().
- It must render framework-specific questions from framework.json.
- POST /register must validate required framework questions.
- The generated installer must write framework answers into the installed .env.
- server/devices.json should store framework, platform, selected_tools, runner_module, and framework_env.
- default capability text should mention the framework description and discovered tools.

Maker integration expectations:

- maker.py should discover the framework and runtime from platforms/.
- python3 -m maker <name> --framework <framework> --platform <runtime> --tools all --framework-env KEY=VALUE should create a runnable generated package.
- Interactive maker runs may prompt framework questions.
- Generated packages should include exp/td.txt, exp/enving.txt, exp/tool_import.txt, and exp/framework_env.txt when framework answers exist.

Verification checklist:

1. Inspect the worktree first:
   git status --short --branch

2. Confirm discovery:
   python3 - <<'PY'
   from maker import discover_frameworks, discover_platforms, discover_tools_for_platform, framework_env_questions
   print(discover_frameworks())
   print(discover_platforms("<framework>"))
   print(discover_tools_for_platform("<runtime>", "<framework>"))
   print([q["name"] for q in framework_env_questions("<runtime>", "<framework>")])
   PY

3. Compile touched Python:
   python3 -m py_compile maker.py server/server.py server/device_client.py platforms/<framework>/core/whatchdog.py

4. Run whitespace check:
   git diff --check

5. Generate a smoke package:
   python3 -m maker tmp_<framework>_smoke --framework <framework> --platform <runtime> --tools all --env-os SmokeOS --env-terminal bash --framework-env KEY=VALUE --force

6. Run the generated wrapper in dry mode if the framework supports it.

7. Remove the generated smoke package:
   rm -rf tmp_<framework>_smoke

8. Check for accidental runtime artifacts:
   git status --short

9. Review the diff:
   git diff

10. Commit and push when verified:
   git add <changed files>
   git diff --cached --check
   git commit -m "Add <framework> framework wrapper"
   git push

Important constraints:

- Keep generated client/comms stable unless explicitly requested.
- Put framework complexity in platforms/<framework>/..., maker.py metadata handling, and server registration/installer plumbing.
- Do not rewrite unrelated Pigion runner behavior while adding a framework.
- Do not leave generated smoke packages, lock files, or cache files staged.
- If the user asks "at the end push everything", commit and push verified changes.
- If the user asks "do not do anything right now", keep the investigation read-only.
```
