# START — Channel Press concurrency recovery (new OpenCode session)

**Workspace:** `E:\newsroom` (same repository, same existing user data).  
**Goal:** Fix the live-use failures uncovered after Queue Build was repaired. This is a narrowly scoped corrective implementation, **not M00–M14 or L00–L09 again**.

## Agent: read order

1. `CONCURRENCY_RECOVERY_CONTRACT.md` — the binding implementation and safety requirements.
2. `CONCURRENCY_FAILURE_EVIDENCE.md` — observed failures and what remains a hypothesis.
3. `CONCURRENCY_ACCEPTANCE_MATRIX.md` — exact repeatable acceptance criteria.
4. The repository's **existing** `AGENTS.md`, `BUILD_SPECIFICATION.md`, `POST_M14_LIVE_COMPLETION.md`, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md`, plus affected source/tests and current git status. Do not start another generic rebuild.

## Paste exactly this message into the new OpenCode session

> Continue the EXISTING Channel Press repository at `E:\newsroom`. Read `START_CONCURRENCY_RECOVERY.md`, `CONCURRENCY_RECOVERY_CONTRACT.md`, `CONCURRENCY_FAILURE_EVIDENCE.md`, and `CONCURRENCY_ACCEPTANCE_MATRIX.md` in full, and apply existing `AGENTS.md`. Investigate and repair the application SQLite write locks, Telethon session-file SQLite locks, conflicting window collectors, web status timeouts, and worker cleanup/launch failures, preserving all existing data, account/session access and channel rights. Work autonomously, reproduce the failures where safely possible, prove root causes with instrumentation, implement focused fixes and regression tests, and verify on Windows with concurrent dashboard operations and an authorized, supervised small live collection if available. Do NOT reset databases, revoke Telegram login, touch `.env` secrets, or enable automatic publishing. Document what is verified versus still blocked; update manuals, commit tested changes and report concrete results. Do not ask for unnecessary information; request only indispensable user intervention or permission.

## Expected handoff at end

- Actual root cause(s), with files/functions/transaction ownership evidence, not guesses.
- Concise file/commit list and independently run commands/test results.
- Current app DB and Telegram session intact; current rights and accounts intact.
- One tested web + worker + Telegram concurrent-use scenario; job-state/retry behavior proven.
- `docs/CONCURRENCY_RECOVERY_REPORT.md` (agent generates this inside the project).
- Exact manual steps for the owner to restart safely and run one supervised cutoff edition.

**Do not put API credentials, phone numbers, login codes or Telethon session data into generated reports.**

# Additional Requirements — Controlled Service Restart and Telegram Preflight

Extend the ongoing Channel Press concurrency recovery with the following mandatory requirements. Read and obey the existing `AGENTS.md`, `CONCURRENCY_RECOVERY_CONTRACT.md`, and acceptance matrix.

## 1. Controlled shutdown and restart

Implement and execute a clean shutdown and restart procedure for all Channel Press-owned services:

- FastAPI/Uvicorn web server.
- Detached edition-building and collection workers.
- Telegram/Telethon connections and their background asyncio tasks.
- Ollama model inference requests and Channel Press-owned model sessions.
- Active scheduler processes or tasks owned by Channel Press.

Before stopping anything, discover and record running processes, process IDs, command lines, ownership, active jobs, locks, and service health.

Gracefully pause or checkpoint active jobs first. Request shutdown, allow outstanding database transactions to commit or roll back, disconnect Telegram clients, close SQLite connections, and unload locally managed AI models.

Terminate a process forcibly only if it is conclusively identified as belonging to Channel Press, fails to stop gracefully, and cannot safely continue. Never terminate unrelated Python programs, browsers, or Ollama workloads belonging to other applications.

Do not delete SQLite databases, WAL/SHM files, Telegram session files, job records, checkpoints, or credentials.

After shutdown, verify that Channel Press no longer has active competing workers or lingering Telegram session owners.

Restart services in a controlled sequence:
1. Validate configuration and database accessibility.
2. Start or verify the Ollama service, without disturbing unrelated workloads.
3. Verify the configured local model is available.
4. Start the Channel Press application.
5. Initialize its managed Telegram connection.
6. Verify that the worker subsystem is healthy and that jobs can be processed without duplicate workers.

Implement reusable, documented PowerShell scripts for safe stop, restart, and service-health checks.

## 2. Mandatory Telegram preflight before Queue Build

Modify the existing Build page and Queue Build backend to perform a short, bounded, read-only Telegram preflight for jobs requiring live Telegram collection.

Verify:

- API ID and API Hash are configured.
- The authenticated Telegram user session is valid.
- Telegram can be reached within a configurable timeout.
- The configured `@channel` can be resolved.
- The authenticated account can access the intended channel and retrieve a minimal, authorized message-history response.
- The connection is not already locked or owned by another incompatible process.
- The relevant source authorization and content-use permissions are valid.
- The requested cutoff interval and channel identity are valid.
- No incompatible collection job is already running for the same window.

A successful preflight must display a clear **Telegram Ready** status with the channel and verification timestamp.

If the preflight fails, do not queue a new live collection job. Display the actual actionable reason, for example:

- Telegram unreachable.
- Telegram authentication expired.
- Channel not accessible.
- Telegram session currently busy.
- Collection already active.
- Source authorization missing.
- Connection timed out.

Do not show only "Internal Server Error."

Perform preflight using the same managed Telegram connection/session architecture as the actual worker. Do not create another independent Telethon client that competes for the SQLite session.

Do not perform long Telegram network calls while holding application database write transactions.

The preflight should be asynchronous or otherwise non-blocking for the web server, with bounded execution time and clear progress/status feedback.

The backend must enforce this requirement even if a user bypasses the frontend button.

Fixture-only or licensed manual-import builds must remain available without a live Telegram connection.

## 3. Connection health after queueing

A successful preflight does not guarantee the connection will remain available.

Therefore, the worker must independently revalidate its Telegram access when collection begins.

If Telegram becomes unavailable afterward:
- Preserve the queued job and its exact cutoff window.
- Apply bounded retries and backoff for transient failures.
- Show a meaningful waiting/retry state.
- Allow manual retry or resume.
- Never advance the completed cutoff watermark prematurely.
- Never silently publish an incomplete edition.

## 4. Dashboard improvements

Add a compact System Health section covering:

- Web server
- Database
- Telegram connection
- Source channel accessibility
- Collection worker
- Ollama service
- Configured AI model
- Active job and last error

Provide controls for **Check Connections** and **Restart Channel Press Services**, with appropriate permissions and confirmation before disruptive actions.

Health checks must distinguish "configured" from "connected and tested." Never display a successful connection merely because credentials are present.

## 5. Verification

Test these situations:

1. Normal shutdown and successful restart.
2. Restart while a collection job is running.
3. Two processes attempting to access the same Telegram session.
4. Missing Telegram credentials.
5. Expired Telegram authentication.
6. Telegram network unavailable.
7. Valid Telegram session but inaccessible channel.
8. Successful preflight followed by network disconnection.
9. Duplicate Queue Build requests.
10. Ollama unavailable while Telegram remains healthy.
11. Simultaneous dashboard operations and active collection.
12. Manual-import build without Telegram connectivity.

Verify that all services and managed models shut down cleanly, no new database locks are introduced, and job checkpoints remain intact.

Run the full existing regression suite, add dedicated tests for the new behavior, and perform one small supervised live collection when access is available.

Update README, USER_MANUAL, ADMIN_GUIDE and TROUBLESHOOTING. Document the actual test results and Git commits in the concurrency recovery report.

**Execute these changes, not merely describe them. Preserve all existing data and do not enable automatic public publication.**