# Pigion

> **Feedback loops feed poops.**

Pigion is an autonomous control platform for the arbitrary devices around you. A watchdog can be installed on a desktop, a 3D printer, a drone, a robot, a programming workstation, or something nobody has written an integration for yet. It can be specialized for one machine or generalized with Pigion's own agent loop.

Each watchdog is a complete standalone agent. It owns its local reasoning, tools, feedback, and execution; it does not need to know that it belongs to a larger system. The orchestrator is a manager, not a central brain. Today it routes goals between people and watchdogs. The intended system will also pass useful observations between otherwise independent agents.

Pigion is built for private homes, labs, disposable systems, and hardware you can recover. It is a platform I use daily, but it is also a learning project with sharp and occasionally absurd edges. It is not polished industrial automation.

## Why It Is Called Pigion

The project started as **Crow**, because crows are smart, observant, and social. Then I used the rough prototype and discovered that it was kind of dumb, so Crow became Pigeon.

The final name came from a watchdog running on a very weak model. Its master prompt said, "You are root." It was given a negative-search task: find an anomaly, even though there was no anomaly to find. After examining the filesystem it concluded:

> There is a `/root` folder which does not contain my source files. This is an anomaly because I am root. Starting removal: `rm -rf /root`.

So Pigeon lost the **e** for "exceptional" and gained an **I** for **"I am root."** It became **Pigion**.

That failure is the point of the motto. Weak agents fail. They misunderstand instructions, invent anomalies, and take bad actions. A feedback loop gives those failures somewhere to go: observe the result, evaluate it, recover, remember what happened, and try again. Enough feedback can make a cheap model with tools appear surprisingly competent. It can also make a new and more elaborate mistake.

## Read This Before Running It

Pigion does not try to be safe.

It can run model-selected shell commands, use sudo credentials, modify or delete files, leak `.env` values, corrupt device state, or take unsafe physical actions through connected hardware. Installing a watchdog means giving an autonomous agent operational control of that device. The dashboard and uninstall flow do not turn that agent into a sandbox.

Use Pigion only on machines and devices you can lose, restore, re-image, or physically contain. Keep backups. Do not expose the controller or installer endpoints to untrusted networks. Do not give a watchdog access to anything you are unwilling to have destroyed or disclosed.

## The Intended Whole-Home System

Imagine one cheap manager and several independent watchdogs:

- A desktop watchdog can create a tiny disk-check helper that calls it when storage becomes concerning, then investigate or repair the problem.
- A 3D-printer watchdog can create a helper around printer status or sensors that calls it after a failed print or abnormal temperature.
- A drone or robot watchdog owns motion, sensors, local decisions, and recovery on its device.
- Other watchdogs can control cameras, appliances, lab hardware, or software services.

The orchestrator tells watchdogs what is currently worth looking out for. A watchdog can create small device-specific helper scripts for those conditions. The helper—not a permanently thinking agent loop—calls the watchdog when a schedule, sensor, webhook, file, process, or other condition fires. The watchdog investigates and reports anything mildly interesting or concerning. The orchestrator decides what matters and selectively injects that context into other watchdogs that may benefit from it.

The watchdogs remain independent. The orchestrator should not absorb their complete internal state or become responsible for every local decision. It manages attention and information between agents that can already operate alone.

This attention/context bus is the next major direction. It is not implemented yet.

## What Works Today

The current repository is a working daily-use prototype with:

- A FastAPI dashboard and JSON-backed device/job registry.
- A local orchestrator worker that accepts goals from the webserver.
- Independent Linux, macOS, and Windows watchdog installation.
- Pluggable framework/runtime manifests with validated configuration questions.
- Required, default, and optional tool policies with per-device selection.
- Framework-owned install, verification, upgrade, and uninstall lifecycle scripts.
- Structured, provenance-bearing architecture profiles shared with each watchdog and summarized for the orchestrator.
- Device heartbeats, queued goals, progress, results, logs, and down-state reporting.
- Remote removal that asks the installed watchdog to uninstall itself before deleting its registry state.
- Pigion's general planning/tool/recovery loop and a GPT Researcher wrapper.

What does **not** exist yet:

- A shared watchdog observation or context bus.
- Orchestrator-directed "look out for this" subscriptions.
- A general helper-trigger convention across frameworks.
- Mature printer, drone, robot, or home-device integrations.
- Industrial reliability, isolation, credential protection, or safety guarantees.

## Architecture

```text
                         goals and future attention guidance
                                      |
                                      v
+------------------+        +-----------------------+
| Web dashboard    |<------>| Orchestrator manager  |
| registry + jobs  |        | routing + coordination|
+--------+---------+        +-----------+-----------+
         |                              |
         | heartbeat / poll / result    | device goals
         v                              v
+------------------+  +------------------+  +------------------+
| Desktop watchdog |  | Printer watchdog |  | Robot watchdog   |
| standalone agent |  | standalone agent |  | standalone agent |
+------------------+  +------------------+  +------------------+
```

The generated `client.py` is deliberately framework-neutral. It heartbeats, polls for a job, calls the selected runner's `run_agent(goal)` (or compatible device function), and posts the result. The selected framework owns local reasoning and execution behind that stable communication contract.

The repository currently contains:

```text
Pigion/
  README.md
  ROADMAP.md
  TOOLDOCS.md
  FRAMEDOCS.md
  maker.py
  requirements.txt
  deploy/
    install.sh
    install.ps1
    setup.sh
    setup.ps1
    uninstall.sh
    uninstall.ps1
  server/
    server.py
    device_client.py
    orchestrator_client.py
    web_store.py
    devices.json
    jobs.json
  orchestrator/
    run_orchestrator.py
    exp/
    tools/
  platforms/
    pigion/
      core/whatchdog.py
      linux/
        framework.json
        tools/
      windows/
        framework.json
        tools/
    gpt_researcher/
      core/whatchdog.py
      linux/
        framework.json
        lifecycle/
        tools/
      windows/
        framework.json
        lifecycle/
        tools/
  tests/
```

The orchestrator and framework watchdogs intentionally remain separate implementations. Consolidating every agent loop into one polished runtime is not a current priority.

## Quick Start

Python 3 is required. The installer can provision it through Homebrew on macOS or through apt, pacman, dnf/yum, zypper, or apk on Linux.

```bash
git clone <your-pigion-repository-url>
cd Pigion
./deploy/install.sh
```

`deploy/install.sh` delegates to `deploy/setup.sh`. The setup operates on the repository root, creates `.venv`, installs `requirements.txt`, prepares configuration/state, and installs the web and orchestrator as systemd services on Linux or user LaunchAgents on macOS. On Windows, run `deploy/setup.ps1` to install user Scheduled Tasks.

The important controller settings are:

```env
ABS_PATH="/absolute/path/to/Pigion"
LLM_PROVIDER="gemini"
LLM_MODEL="gemini-2.5-flash-lite"
LLM_API_KEY="your-model-api-key"
PIGION_ORCHESTRATOR_USER="admin"
PIGION_ORCHESTRATOR_PASSWORD="change-this"
PIGION_SERVER_URL="http://127.0.0.1:8000"
```

The built-in Pigion runtime supports `gemini`, `openai`, and `ollama`. Provider-specific variables include `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OLLAMA_HOST`. Frameworks that do not use Pigion's model layer define their own questions in `framework.json` instead.

The installer normally binds the webserver to `0.0.0.0`. When Tailscale is available, setup prefers its IPv4 address for generated device installers. Override the advertised address with:

```bash
PIGION_PUBLIC_HOST=100.x.y.z ./deploy/install.sh
```

Open:

```text
http://<controller-address>:8000/login
```

The repository defaults are `admin` / `pigion`; change them before treating the controller as anything other than a local experiment.

To remove the repo-local web and orchestrator services without deleting repository data:

```bash
./deploy/uninstall.sh
```

Windows `deploy/setup.ps1` prepares the local Python environment and registers the controller processes as Scheduled Tasks. Use `deploy/uninstall.ps1` to remove those tasks. The uninstall scripts keep repository data and the virtual environment.

## Registering a Watchdog

The dashboard registration workflow is:

1. Choose a watchdog name and framework/runtime.
2. The webapp loads that runtime's validated `framework.json` specification.
3. Configure the device OS/terminal and answer framework-owned questions.
4. Choose tools according to the manifest:
   - `required`: always selected and locked.
   - `default`: initially selected but removable.
   - `optional`: available but initially disabled.
5. For the Pigion framework, configure Gemini, OpenAI, or Ollama. Other frameworks can own their model configuration.
6. Provide the target sudo password when Linux installation needs it. macOS and Windows watchdog installs are user-scoped.
7. Register the device and run the generated Linux, macOS, or Windows installer command on the target.

Registration does not update the Git checkout. It uses the framework definitions from the controller's currently deployed revision, creates only the selected tool package, records the manifest revision, and generates a device-specific installer.

Linux devices are installed under `/opt/pigion/DEVICE_NAME` by default and run as a systemd service. macOS devices are installed under `~/Library/Application Support/Pigion/DEVICE_NAME` and run as a user LaunchAgent. Windows devices are installed under `%LOCALAPPDATA%\Pigion\DEVICE_NAME` and run through a user Scheduled Task. `PIGION_INSTALL_ROOT` overrides the device location.

Framework lifecycle scripts run during installation before the watchdog starts. These are trusted arbitrary repository scripts, not sandboxed plugins. A framework can therefore be as destructive as the watchdog it installs.

The detailed wrapper, manifest, tool-policy, and lifecycle contract is in [FRAMEDOCS.md](FRAMEDOCS.md).

## Sending and Coordinating Goals

The Send Goal page currently supports:

- **Local Orchestrator**: queue a high-level goal for `server/orchestrator_client.py`, which starts a fresh orchestrator process.
- **Registered watchdog**: queue a goal directly for one device UUID.

Registering a watchdog also creates an orchestrator tool such as:

```text
device_workshop_printer:Inspect the last failed print and report the likely cause
```

The generated tool hides the UUID and lets the orchestrator address the watchdog by its registered name. This is current goal routing, not yet the planned observation/context system.

## Device Communication

Watchdogs use HTTP polling rather than WebSockets:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/jobs/<device_uuid>` | Fetch the next waiting device job. |
| `POST` | `/api/jobs/<job_id>/started` | Mark a job running. |
| `POST` | `/api/jobs/<job_id>/progress` | Update progress and append logs. |
| `POST` | `/api/jobs/<job_id>/finished` | Store a result or failure. |
| `POST` | `/api/device/<uuid>/heartbeat` | Refresh watchdog availability. |
| `POST` | `/api/device/<uuid>/logs` | Append watchdog/job logs. |
| `POST` | `/api/orchestrator/goals` | Queue a goal for the local orchestrator. |
| `GET` | `/api/frameworks/<framework>/<runtime>` | Return an authenticated registration specification. |

The stable client contract is intentionally small so different agent frameworks can sit behind it without changing heartbeat, job, result, or removal behavior.

## Removing a Watchdog

Removing a registered watchdog queues a special uninstall job. The watchdog launches its framework uninstall lifecycle, then its shared Linux, macOS, or Windows cleanup script. The server removes registry state, queued work, generated packages, installer artifacts, and the orchestrator tool only after the device acknowledges the uninstall.

If the device is offline, it remains registered with uninstall pending so it can receive that job later.

## Pigion's Built-In General Watchdog

`platforms/pigion/core/whatchdog.py` is the source of truth for the generalized Pigion loop. `maker.py` copies it into each generated device package and supplies only the tools selected for that watchdog. The runner:

1. Formalizes a goal when enabled.
2. Builds a short plan.
3. Chooses and executes one tool action at a time.
4. Evaluates progress after every action.
5. Handles interactive shell sessions.
6. Searches prior failure/recovery examples.
7. Retries, replaces steps, or continues until completion or a configured limit.

For Gemini, action selection, recovery selection, and persistent-session input use native function calls with SDK-side
automatic execution disabled. The runner converts exactly one returned function call into its existing internal action
shape, then keeps the same tool execution, evaluation, recovery, and persistence loop. OpenAI and Ollama retain the
textual action protocol as a compatibility path.

Its tools include shell execution, search/URL extraction, temporary memory, permanent memory in supported platform packages, direct user questions, and final return handling. Tool imports—and Gemini function declarations—are still derived from the brittle `Command - prefix:` documentation format.

Generated packages expose the runner through `run_agent(goal)` and are normally called by the framework-neutral device client. For local wrapper development, generate a disposable instance with `maker.py` rather than maintaining another copied runner:

```bash
python3 -m maker local_watchdog \
  --framework pigion \
  --platform linux \
  --tools shell,search,return \
  --env-os Linux \
  --env-terminal bash
```

See [TOOLDOCS.md](TOOLDOCS.md) for the current tool contract and runtime details.

## Adding Frameworks and Devices

Experimental contributions are welcome. A framework/runtime supplies:

- A versioned `framework.json` manifest.
- A standalone runner exposing `run_agent(goal)` or its compatible device entrypoint.
- Required/default/optional tool modules and documentation.
- Framework-specific registration questions.
- Optional install, verify, upgrade, and uninstall lifecycle scripts.
- Honest documentation of capabilities, assumptions, privileges, and failure modes.

Lifecycle scripts and tool modules are trusted code with arbitrary execution. Manifest validation prevents path traversal and malformed definitions; it does not determine whether a repository script is malicious or sensible.

Read [FRAMEDOCS.md](FRAMEDOCS.md) before adding a wrapper. Read [ROADMAP.md](ROADMAP.md) before designing shared trigger or coordination abstractions: helper patterns should first be proven inside real framework/device integrations.

## Roadmap

The roadmap has no dates and makes no polished-product commitments. Each phase separates current capability, the next concrete milestone, and longer-term experimentation. The same roadmap is maintained in [ROADMAP.md](ROADMAP.md).

### 1. Orchestrator Attention and Context

**Current:** The orchestrator can route queued goals to named watchdogs and receive their results. It does not collect unsolicited observations or redistribute context.

**Next milestone:** Build the first shared attention/context path without moving device reasoning into the orchestrator.

- Let the orchestrator publish what watchdogs should look out for.
- Let watchdogs report interesting observations, concerns, goal results, and failures.
- Add orchestrator-side filtering, relevance decisions, targeted context injection, provenance, and loop prevention.
- Preserve watchdog independence; do not share complete internal state or turn the orchestrator into the reasoning brain.

### 2. Architecture and Limitation Awareness

**Current:** Framework and device profiles declare capabilities, unavailable actions, observable state, dependencies, privilege boundaries, physical constraints, known failure modes, and uncertainty. The full profile is injected into the watchdog; heartbeat updates give the orchestrator a routing view. Facts retain `declared`, `observed`, or `inferred` provenance, and runtime recovery records observed failure modes.

**Next milestone:** Refine profiles from broader real-device use and improve automatic changed-state observations without turning architectural self-knowledge into a safety or approval layer.

- Let each framework/device declare capabilities, unavailable actions, observable state, required dependencies, privilege boundaries, physical constraints, known failure modes, and uncertainty.
- Inject the local profile into the watchdog so it understands its own body, tools, blind spots, and architectural limits before planning.
- Give the orchestrator a concise version of every watchdog profile so it can route goals and context without assuming unsupported capabilities.
- Let watchdogs and the orchestrator report when a request exceeds known limits instead of silently inventing a capability; this is architectural self-knowledge, not a safety or approval layer.
- Update profiles from real failures and changed device state while retaining provenance for whether a limit was declared, observed, or inferred.

### 3. Mixed Physical and Digital Integrations

**Current:** Pigion has generalized computer watchdogs and a GPT Researcher wrapper, but not the representative physical-device set.

**Next milestone:** Coordinate three substantially different real devices and use their failures to shape later abstractions.

- Prove coordination using a desktop/programming watchdog, a 3D-printer watchdog, and a mobile, drone, or robot watchdog.
- Define each integration through its own framework manifest, tools, lifecycle, observations, and operating assumptions.
- Use these real integrations to discover coordination and helper-trigger patterns rather than designing them entirely in advance.

### 4. Framework-Owned Trigger Helpers

**Current:** The communication client polls for externally queued goals. A framework or watchdog can create its own scripts, but Pigion has no documented convention for small helpers that wake or call an agent.

**Next milestone:** Let watchdogs create and manage tiny device-specific helper scripts, then prove that pattern in several wrappers before extracting shared behavior into the platform.

- Let helpers watch device-specific schedules, sensors, webhooks, files, processes, and other conditions, then call the watchdog with a focused goal or context when something happens.
- Keep helpers small and deterministic; they detect and notify, while the watchdog performs the autonomous reasoning and response.
- Extract a common helper invocation and management convention only after multiple framework implementations demonstrate reusable behavior.

### 5. ShadowFS

**Current:** Model-selected file changes normally touch the host filesystem directly.

**Next milestone:** Add an inspectable mutation layer that improves experimentation and feedback without being marketed as a security sandbox.

- Make staged filesystem mutation a core milestone.
- Let watchdogs mutate a shadow workspace, inspect consequences, and commit or discard changes.
- Present ShadowFS as a feedback and experimentation mechanism, not a promise that Pigion becomes safe.

### 6. Maintenance and Developer Experience

**Current:** The platform works around several known parser, entrypoint, tool-contract, and runtime-state inconsistencies.

**Next milestone:** Repair the issues that obstruct coordination and new integrations while retaining independent runtime implementations.

- Repair the CLI/function entrypoint, brittle tool-document parser, `askuser` contract, search return shape, and memory retrieval behavior.
- Keep the orchestrator and framework runtimes independent rather than prioritizing a single universal agent core.
- Improve diagnostics and extension documentation only where they unblock coordination, integrations, or trigger helpers.

### 7. Longer-Term Experiments

**Current:** The complete controller already runs on Raspberry Pi Zero 2 W-class hardware and can use local or low-cost model providers.

**Direction:** Keep testing how far cheap hardware, weak models, tools, and repeated feedback can be pushed without pretending that experimentation produces industrial reliability.

- Explore cheap-model competence through retries, tools, recovery, observation, and cross-device context.
- Continue targeting extremely inexpensive infrastructure, including Raspberry Pi Zero-class controllers and local or low-cost inference.
- Explore broader household autonomy without promising industrial reliability, centralized safety, or production hardening.

## Expectations

No maturity or safety guarantee is implied by the existence of installers, a dashboard, or daily personal use. Treat every watchdog and integration as experimental trusted code. If you build something interesting with it, document what it controls, what it can destroy, and what feedback lets it recover.
