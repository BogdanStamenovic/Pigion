# Pigion Roadmap

Pigion is a daily-use learning platform for autonomous watchdogs across cheap household and lab devices. This roadmap has no dates and makes no polished-product commitments. Each phase separates current capability, the next concrete milestone, and longer-term experimentation.

## 1. Orchestrator Attention and Context

**Current:** The orchestrator can route queued goals to named watchdogs and receive their results. It does not collect unsolicited observations or redistribute context.

**Next milestone:** Build the first shared attention/context path without moving device reasoning into the orchestrator.

- Let the orchestrator publish what watchdogs should look out for.
- Let watchdogs report interesting observations, concerns, goal results, and failures.
- Add orchestrator-side filtering, relevance decisions, targeted context injection, provenance, and loop prevention.
- Preserve watchdog independence; do not share complete internal state or turn the orchestrator into the reasoning brain.

## 2. Architecture and Limitation Awareness

**Current:** Framework manifests describe tools, configuration, lifecycle scripts, and supported operating systems, but neither the watchdog nor orchestrator receives a complete model of what the device can do, cannot do, cannot observe, or is likely to misunderstand.

**Next milestone:** Make limitations part of the agent architecture instead of leaving them as prose or discovering them only after failure.

- Let each framework/device declare capabilities, unavailable actions, observable state, required dependencies, privilege boundaries, physical constraints, known failure modes, and uncertainty.
- Inject the local profile into the watchdog so it understands its own body, tools, blind spots, and architectural limits before planning.
- Give the orchestrator a concise version of every watchdog profile so it can route goals and context without assuming unsupported capabilities.
- Let watchdogs and the orchestrator report when a request exceeds known limits instead of silently inventing a capability; this is architectural self-knowledge, not a safety or approval layer.
- Update profiles from real failures and changed device state while retaining provenance for whether a limit was declared, observed, or inferred.

## 3. Mixed Physical and Digital Integrations

**Current:** Pigion has generalized computer watchdogs and a GPT Researcher wrapper, but not the representative physical-device set.

**Next milestone:** Coordinate three substantially different real devices and use their failures to shape later abstractions.

- Prove coordination using a desktop/programming watchdog, a 3D-printer watchdog, and a mobile, drone, or robot watchdog.
- Define each integration through its own framework manifest, tools, lifecycle, observations, and operating assumptions.
- Use these real integrations to discover coordination and helper-trigger patterns rather than designing them entirely in advance.

## 4. Framework-Owned Trigger Helpers

**Current:** The communication client polls for externally queued goals. A framework or watchdog can create its own scripts, but Pigion has no documented convention for small helpers that wake or call an agent.

**Next milestone:** Let watchdogs create and manage tiny device-specific helper scripts, then prove that pattern in several wrappers before extracting shared behavior into the platform.

- Let helpers watch device-specific schedules, sensors, webhooks, files, processes, and other conditions, then call the watchdog with a focused goal or context when something happens.
- Keep helpers small and deterministic; they detect and notify, while the watchdog performs the autonomous reasoning and response.
- Extract a common helper invocation and management convention only after multiple framework implementations demonstrate reusable behavior.

## 5. ShadowFS

**Current:** Model-selected file changes normally touch the host filesystem directly.

**Next milestone:** Add an inspectable mutation layer that improves experimentation and feedback without being marketed as a security sandbox.

- Make staged filesystem mutation a core milestone.
- Let watchdogs mutate a shadow workspace, inspect consequences, and commit or discard changes.
- Present ShadowFS as a feedback and experimentation mechanism, not a promise that Pigion becomes safe.

## 6. Maintenance and Developer Experience

**Current:** The platform works around several known parser, entrypoint, tool-contract, and runtime-state inconsistencies.

**Next milestone:** Repair the issues that obstruct coordination and new integrations while retaining independent runtime implementations.

- Repair the CLI/function entrypoint, brittle tool-document parser, `askuser` contract, search return shape, and memory retrieval behavior.
- Keep the orchestrator and framework runtimes independent rather than prioritizing a single universal agent core.
- Improve diagnostics and extension documentation only where they unblock coordination, integrations, or trigger helpers.

## 7. Longer-Term Experiments

**Current:** The complete controller already runs on Raspberry Pi Zero 2 W-class hardware and can use local or low-cost model providers.

**Direction:** Keep testing how far cheap hardware, weak models, tools, and repeated feedback can be pushed without pretending that experimentation produces industrial reliability.

- Explore cheap-model competence through retries, tools, recovery, observation, and cross-device context.
- Continue targeting extremely inexpensive infrastructure, including Raspberry Pi Zero-class controllers and local or low-cost inference.
- Explore broader household autonomy without promising industrial reliability, centralized safety, or production hardening.
