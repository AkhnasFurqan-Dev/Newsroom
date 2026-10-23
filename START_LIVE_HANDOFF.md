# Channel Press — What to do after M14

1. Place `POST_M14_LIVE_COMPLETION.md` inside the existing `E:\newsroom` repository (do not replace `AGENTS.md` or any previous document).
2. Place `LIVE_ENV_PLACEHOLDERS.env.template` there too. The agent must reconcile its variable names with the actual application configuration.
3. Start OpenCode in **the same repository**, preferably a fresh session with access to the full Git history.
4. Send only this command:

   > Read `POST_M14_LIVE_COMPLETION.md` and execute its **SINGLE COMMAND FOR THE CODING AGENT** and all L00–L09 phases. Continue from the existing completed M14 codebase, preserve data, verify every claim, and only pause for my private `.env` values, Telegram user login, legally required source rights/consent, or approval to publish. Complete the rest autonomously and update the current manuals and Git history.

5. Once the agent has generated `E:\newsroom\.env`, manually fill API ID and Hash in that file; do not paste them into chat. Use the local app for secure verification code and 2FA prompts.
6. Before enabling unattended daily builds, inspect and approve a full rights-cleared cutoff-to-cutoff edition.

`[previous_cutoff, current_cutoff)` is the non-negotiable normal-edition window. The initial 30-day import is **context only**. Failure/retry must not silently skip windows or duplicate articles.
