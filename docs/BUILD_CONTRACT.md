# Original build contract (archived)

This document preserves the handoff package that defined the build. It is not the
product manual. The finished application and its verified limits are described in
[`README.md`](../README.md), [`USER_MANUAL.md`](../USER_MANUAL.md),
[`ADMIN_GUIDE.md`](../ADMIN_GUIDE.md) and [`docs/QA_REPORT.md`](QA_REPORT.md).

---

# Newspaper App — Development Handoff Package

This is **not** the finished newspaper application. It is a concrete build contract intended to be placed in a blank repository and supplied to OpenCode or another coding agent. The agent is required to create the real program and its actual user-facing README and how-to manual.

## Files

- **`START_HERE.md`** — exact kickoff prompt and agent instructions.
- **`BUILD_SPECIFICATION.md`** — product specification, architecture, modules, database, workflows, media, mapping, publishing, security and milestones.
- **`AGENTS.md`** — durable cross-model coding and editorial standards; keep it at the repository root.
- **`ACCEPTANCE_TESTS.md`** — contract tests and criteria for calling the project complete.

## Use with OpenCode

1. Create an empty folder (e.g. `channel-press`) and copy the four build-contract files (`START_HERE.md`, `BUILD_SPECIFICATION.md`, `AGENTS.md`, `ACCEPTANCE_TESTS.md`) into it.
2. Open the folder in OpenCode or your chosen AI coding environment.
3. Copy the prompt under **Message to paste into OpenCode / coding agent** in `START_HERE.md`, or simply say: `Read START_HERE.md and implement M00–M14 from BUILD_SPECIFICATION.md; obey AGENTS.md and ACCEPTANCE_TESTS.md. Work autonomously.`
4. Allow the agent to create/update source files, install **free** dependencies and run tests. Let it ask for consent/credentials only when needed for live Telegram access; without them, it must finish a functioning fixtures/import-based application.
5. Verify the final PDF/e-paper, documented commands, user manual and test report before using real source material.

## Practical notes

- The default uses the Windows laptop's hardware rather than depending on ongoing cloud bills.
- Telegram's licensing/AI conditions require careful authorization. Source rights and content-contributor consent must not be treated as optional formalities.
- Publicly reachable QR landing pages need a real publication destination. Without one, the system uses honest offline/Telegram-link fallbacks.
- The `.md` handoff files themselves are only a specification; they do not contain Telegram credentials or machine-specific secrets.
