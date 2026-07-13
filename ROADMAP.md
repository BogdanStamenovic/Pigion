# Pigion Roadmap

Pigion is a daily-use learning platform for autonomous watchdogs across cheap household and lab devices. This roadmap has no dates and makes no polished-product commitments. Each phase separates current capability, the next concrete milestone, and longer-term experimentation.

## 1. Orchestrator Attention and Context

**Current:** The orchestrator can route queued goals to named watchdogs and receive their results. It does not collect unsolicited observations or redistribute context.

**Next milestone:** Build the first shared attention/context path without moving device reasoning into the orchestrator.

- Let the orchestrator publish what watchdogs should look out for.
- Let watchdogs report interesting observations, concerns, goal results, and failures.
- Add orchestrator-side filtering, relevance decisions, targeted context injection, provenance, and loop prevention.
- Preserve watchdog independence; do not share complete internal state or turn the orchestrator into the reasoning brain.

## 2. Mixed Physical and Digital Integrations

**Current:** Pigion has generalized computer watchdogs and a GPT Researcher wrapper, but not the representative physical-device set.

**Next milestone:** Coordinate three substantially different real devices and use their failures to shape later abstractions.

- Prove coordination using a desktop/programming watchdog, a 3D-printer watchdog, and a mobile, drone, or robot watchdog.
- Define each integration through its own framework manifest, tools, lifecycle, observations, and operating assumptions.
- Use these real integrations to discover coordination and event patterns rather than designing them entirely in advance.

## 3. Framework-Owned Event Autonomy

**Current:** The communication client polls for externally queued goals. Frameworks may implement private behavior, but Pigion has no documented event-autonomy contract.

**Next milestone:** Prove proactive behavior inside several wrappers before extracting anything into the shared platform.

- Allow makers and end users to define device-specific schedules, sensors, webhooks, files, processes, and other triggers inside wrappers.
- Support persistent missions and event-driven work without requiring every action to begin as a queued user goal.
- Extract a common Pigion event contract only after multiple framework implementations demonstrate reusable behavior.

## 4. ShadowFS

**Current:** Model-selected file changes normally touch the host filesystem directly.

**Next milestone:** Add an inspectable mutation layer that improves experimentation and feedback without being marketed as a security sandbox.

- Make staged filesystem mutation a core milestone.
- Let watchdogs mutate a shadow workspace, inspect consequences, and commit or discard changes.
- Present ShadowFS as a feedback and experimentation mechanism, not a promise that Pigion becomes safe.

## 5. Maintenance and Developer Experience

**Current:** The platform works around several known parser, entrypoint, tool-contract, and runtime-state inconsistencies.

**Next milestone:** Repair the issues that obstruct coordination and new integrations while retaining independent runtime implementations.

- Repair the CLI/function entrypoint, brittle tool-document parser, `askuser` contract, search return shape, and memory retrieval behavior.
- Keep Pi, laptop, orchestrator, and framework runtimes independent rather than prioritizing a shared-core refactor.
- Improve diagnostics and extension documentation only where they unblock coordination, integrations, or events.

## 6. Longer-Term Experiments

**Current:** The complete controller already runs on Raspberry Pi Zero 2 W-class hardware and can use local or low-cost model providers.

**Direction:** Keep testing how far cheap hardware, weak models, tools, and repeated feedback can be pushed without pretending that experimentation produces industrial reliability.

- Explore cheap-model competence through retries, tools, recovery, observation, and cross-device context.
- Continue targeting extremely inexpensive infrastructure, including Raspberry Pi Zero-class controllers and local or low-cost inference.
- Explore broader household autonomy without promising industrial reliability, centralized safety, or production hardening.
