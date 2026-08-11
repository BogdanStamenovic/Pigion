# Pigion Roadmap

Pigion is a daily-use learning platform for autonomous watchdogs across cheap household and lab devices. This roadmap has no dates and makes no polished-product commitments. Each phase separates current capability, the next concrete milestone, and longer-term experimentation.

## 1. Pigion Todo and Phone Shortcut Protocol

**Current:** `pigion-todo` and its iPhone Shortcut interface exist outside Pigion's framework and device communication model.

**Next milestone:** Integrate `pigion-todo` as a first-class Pigion framework and turn its iPhone Shortcut interface into the first phone-to-Pigion communication protocol.

- Package `pigion-todo` with a framework manifest, lifecycle, runner, tools, and explicit operating assumptions.
- Preserve its existing task-management behavior while routing requests and results through Pigion's normal framework contract.
- Define the iPhone Shortcut request, authentication, device identity, job submission, status, and result shapes as a reusable phone communication protocol.
- Prove the protocol through the todo framework before generalizing it to other phone-triggered watchdogs.

## 2. Deployment Updates Through `auto-update-changer`

**Current:** Pigion records framework revisions when generating deployments, but updating deployed watchdog code is not a primary, uniform lifecycle.

**Next milestone:** Make `auto-update-changer` the main method for distributing and applying Pigion deployment updates.

- Integrate `auto-update-changer` into controller and watchdog deployment lifecycles.
- Detect the deployed revision, distribute only the intended update, apply it, and verify the resulting service state.
- Define rollback and failed-update reporting before treating an update as complete.
- Keep fresh installation and removal separate from routine deployed-code updates.

## 3. Drag-and-Drop Deployment Changes

**Current:** Files added to a controller-side spawned watchdog machine/package are not automatically reflected on the corresponding installed deployment.

**Next milestone:** Make a file appearing inside spawned machine X propagate to the actual deployment represented by machine X.

- Watch each spawned machine workspace for added or changed files.
- Map the workspace to exactly one registered deployment and preserve relative paths and file metadata where supported.
- Transfer changes through the normal update path, apply them on the deployment, and report verification or conflict failures.
- Coalesce bursts of file changes so drag-and-drop updates do not produce an update job per filesystem event.

## 4. Direct/Relay-Aware Package Transfer

**Current:** Pigion does not select a large-package transport based on whether Tailscale has a direct peer-to-peer UDP path or is using a relay.

**Next milestone:** Before sending a large package, determine whether the target has a direct Tailscale UDP connection; use that direct path when available and use `fiotransfer` as the transfer source when the connection is relayed.

- Add a deterministic pre-transfer check that distinguishes a direct UDP peer path from a Tailscale relay/DERP path.
- Keep ordinary direct transfers on the Tailscale peer connection.
- Integrate `fiotransfer` as the relay fallback for large packages and return a bounded retrieval reference to the target deployment.
- Install and verify `fiotransfer` automatically on every supported deployment through framework/deployment lifecycle hooks.
- Verify checksums and package identity after either transfer path before applying an update.

## 5. Cron Jobs and Pigion Hooks

**Current:** Frameworks can ship lifecycle scripts and watchdogs can create helper scripts, but cron jobs and hooks do not yet have a common Pigion contract.

**Next milestone:** Define and implement scheduled jobs and event hooks before integrating the Ambient Transcribing System.

- Define how frameworks declare, install, update, invoke, inspect, and remove cron jobs or platform-equivalent schedules.
- Define a small Pigion hook payload for event source, timestamp, device identity, context, deduplication, and requested watchdog attention.
- Route hook invocations through normal watchdog jobs instead of creating a second agent-control path.
- Prove hook cleanup and idempotent reinstall behavior across supported operating systems.

## 6. Ambient Transcribing System Hook

**Current:** `pigion-ATS` does not clearly communicate its actual purpose and is not integrated through a standard Pigion hook.

**Next milestone:** Rename and align `pigion-ATS` as the Ambient Transcribing System (ATS), then integrate it as a Pigion hook consumer/producer using the cron and hook contract above.

- Update repository, service, package, and documentation names so ATS consistently means Ambient Transcribing System.
- Define which ambient transcription events are noteworthy enough to emit a Pigion hook.
- Send bounded transcript context and provenance through the hook without treating the orchestrator as the transcription engine.
- Let a receiving watchdog decide what action, storage, or escalation the transcription event requires.

## 7. Orchestrator Attention and Context

**Current:** The orchestrator can route queued goals to named watchdogs and receive their results. It does not collect unsolicited observations or redistribute context.

**Next milestone:** Build the first shared attention/context path without moving device reasoning into the orchestrator.

- Let the orchestrator publish what watchdogs should look out for.
- Let watchdogs report interesting observations, concerns, goal results, and failures.
- Add orchestrator-side filtering, relevance decisions, targeted context injection, provenance, and loop prevention.
- Preserve watchdog independence; do not share complete internal state or turn the orchestrator into the reasoning brain.

## 8. Architecture and Limitation Awareness

**Current:** Framework and device profiles declare capabilities, unavailable actions, observable state, dependencies, privilege boundaries, physical constraints, known failure modes, and uncertainty. Watchdog prompts receive concise role-specific capability or dependency context rather than the full profile; heartbeat updates give the orchestrator a routing view. Facts retain `declared`, `observed`, or `inferred` provenance, and runtime recovery records observed failure modes.

**Next milestone:** Refine profiles from broader real-device use and improve automatic changed-state observations without turning architectural self-knowledge into a safety or approval layer.

- Let each framework/device declare capabilities, unavailable actions, observable state, required dependencies, privilege boundaries, physical constraints, known failure modes, and uncertainty.
- Give each watchdog call only the concise capability, dependency, or runtime-architecture facts needed for that call's role.
- Give the orchestrator a concise version of every watchdog profile so it can route goals and context without assuming unsupported capabilities.
- Let watchdogs and the orchestrator report when a request exceeds known limits instead of silently inventing a capability; this is architectural self-knowledge, not a safety or approval layer.
- Update profiles from real failures and changed device state while retaining provenance for whether a limit was declared, observed, or inferred.

## 9. Mixed Physical and Digital Integrations

**Current:** Pigion has generalized computer watchdogs and a GPT Researcher wrapper, but not the representative physical-device set.

**Next milestone:** Coordinate three substantially different real devices and use their failures to shape later abstractions.

- Prove coordination using a desktop/programming watchdog, a 3D-printer watchdog, and a mobile, drone, or robot watchdog.
- Define each integration through its own framework manifest, tools, lifecycle, observations, and operating assumptions.
- Use these real integrations to discover coordination and helper-trigger patterns rather than designing them entirely in advance.

## 10. Framework-Owned Trigger Helpers

**Current:** The communication client polls for externally queued goals. A framework or watchdog can create its own scripts, but Pigion has no documented convention for small helpers that wake or call an agent.

**Next milestone:** Let watchdogs create and manage tiny device-specific helper scripts, then prove that pattern in several wrappers before extracting shared behavior into the platform.

- Let helpers watch device-specific schedules, sensors, webhooks, files, processes, and other conditions, then call the watchdog with a focused goal or context when something happens.
- Keep helpers small and deterministic; they detect and notify, while the watchdog performs the autonomous reasoning and response.
- Extract a common helper invocation and management convention only after multiple framework implementations demonstrate reusable behavior.

## 11. ShadowFS

**Current:** Model-selected file changes normally touch the host filesystem directly.

**Next milestone:** Add an inspectable mutation layer that improves experimentation and feedback without being marketed as a security sandbox.

- Make staged filesystem mutation a core milestone.
- Let watchdogs mutate a shadow workspace, inspect consequences, and commit or discard changes.
- Present ShadowFS as a feedback and experimentation mechanism, not a promise that Pigion becomes safe.

## 12. Maintenance and Developer Experience

**Current:** The platform works around several known parser, entrypoint, tool-contract, and runtime-state inconsistencies.

**Next milestone:** Repair the issues that obstruct coordination and new integrations while retaining independent runtime implementations.

- Repair the CLI/function entrypoint, brittle tool-document parser, `askuser` contract, search return shape, and memory retrieval behavior.
- Keep the orchestrator and framework runtimes independent rather than prioritizing a single universal agent core.
- Improve diagnostics and extension documentation only where they unblock coordination, integrations, or trigger helpers.

## 13. Longer-Term Experiments

**Current:** The complete controller already runs on Raspberry Pi Zero 2 W-class hardware and can use local or low-cost model providers.

**Direction:** Keep testing how far cheap hardware, weak models, tools, and repeated feedback can be pushed without pretending that experimentation produces industrial reliability.

- Explore cheap-model competence through retries, tools, recovery, observation, and cross-device context.
- Continue targeting extremely inexpensive infrastructure, including Raspberry Pi Zero-class controllers and local or low-cost inference.
- Explore broader household autonomy without promising industrial reliability, centralized safety, or production hardening.
