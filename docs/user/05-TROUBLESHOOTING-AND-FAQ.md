# Newsroom — Troubleshooting & FAQ

**Product:** Newsroom 1.0.0 for Windows  
**Audience:** Owners, editors and operators using the installed application  
**Last verified against:** the packaged build of 31 October 2026

Use this manual when something does not behave as expected. Start with the symptom table, then follow the detailed section. Prefer safe, reversible steps. Do not delete the database, the Telegram session, `.env` or the data folder to "fix" a problem unless a section explicitly says to restore from a verified backup.

> **Before you change anything:** if the application still opens, go to **Connections** and **Diagnostics** and note the exact status and message. If a job failed, open the job and note its number, stage and error text. That information makes the next step much safer.

---

## 1. Quick symptom table

| Symptom | Likely area | Go to |
|---|---|---|
| Installer does not start, or stops with a WebView2 message | Windows runtime / installer | §2 |
| Window opens but never reaches the sign-in page | Startup / WebView2 / database | §3 |
| Cannot sign in, or account is locked | Sign-in | §4 |
| Second launch does not open a second window | Single-instance design | §5 |
| Closing the window seems to hang | Worker checkpoint | §5 |
| **Sources** says Telegram credentials are missing | Live Telegram configuration | §6 |
| Telegram login code does not arrive, or login expires | Telegram authentication | §6 |
| Telegram says the channel is private or inaccessible | Account membership / rights | §6 |
| **AI Models** says runtime unavailable | Ollama service | §7 |
| Model download fails, stops at a percentage, or runs out of disk | Model transfer | §7 |
| Model is installed but drafting refuses to start | Model test / digest | §7 |
| Build stays queued or a job seems stuck | Worker / job | §8 |
| Build says **Awaiting review** and will not release | Editorial review | §9 |
| Preview PDF is old, blank or does not refresh | Preview / renderer | §10 |
| Text is clipped, pages are blank or underfull | Layout / copy / theme | §10 |
| Images do not appear | Media cache / source media | §11 |
| Urdu or another script appears in the wrong font | Font coverage | §11 |
| QR code or source appendix is missing | Public evidence URL / source code policy | §12 |
| Database temporarily busy message | Concurrent write | §13 |
| The computer restarted during a build | Job recovery | §8 |
| A schedule did not run by itself | Scheduler limitation | §14 |
| Need to recover lost data | Backup / restore | §15 |
| Need to send a problem report | Logs and privacy | §16 |

---

## 2. The installer does not start or stops early

### The installer is blocked by Windows

**Possible cause:** Windows SmartScreen or an organisational policy does not recognise the publisher. This build is not code-signed.

**Safe steps:**

1. Confirm you have the `NewsroomSetup.exe` that belongs to this release. Do not use a copy from an unknown source.
2. In the SmartScreen prompt, choose **More info** and then **Run anyway** only if you obtained the file from the owner or the approved release location.
3. If an organisation manages the PC, ask its administrator before overriding a policy.

### Setup says WebView2 could not be installed

**Possible cause:** no internet connection, a proxy or firewall blocks Microsoft's download, or WebView2 installation is disabled by policy.

**Safe steps:**

1. Connect to the internet and run setup again.
2. If the PC uses a proxy, make sure Windows and Microsoft Edge can reach the internet.
3. If the runtime is already installed but setup does not detect it, install the current Microsoft Edge WebView2 Evergreen Runtime from Microsoft, then run `NewsroomSetup.exe` again.
4. If policy blocks the runtime, ask the administrator to allow it. Newsroom cannot render its window without WebView2.

### Setup stops on an existing installation

**Possible cause:** Newsroom (or the earlier Channel Press) is already installed for this Windows account.

**Safe steps:**

1. Read the prompt. It should say that application files will be updated in place and that personal data, Telegram sessions, settings and editions are stored separately.
2. Choose **Yes** to update. Do not uninstall first.
3. If setup refuses because the application is running, close Newsroom with **Pause safely and exit**, then retry.

See **Installation & Maintenance** for the full upgrade and uninstall behaviour.

---

## 3. The window opens but never reaches sign-in

### The window shows a startup error

**Possible cause:** the local service did not start, the database could not be opened, or a migration could not complete.

**Safe steps:**

1. Close the error window. Do not delete the data folder.
2. Open `%LOCALAPPDATA%\ChannelPress\logs` in File Explorer.
3. Open the newest `desktop-YYYYMMDD.log` and read the last few lines. A message such as `desktop_start_failed` or `desktop_api_shutdown_timeout` identifies the failing stage.
4. Make sure no other Newsroom process is running: open Task Manager, look for **Newsroom** and **NewsroomWorker**, and end only those tasks if they are unresponsive.
5. Start Newsroom again. If it still fails, copy the log file for support as described in §16.

### The window is blank or white

**Possible cause:** WebView2 failed to initialise, or the local page was interrupted.

**Safe steps:**

1. Close Newsroom with **Pause safely and exit** if the window responds.
2. Reopen it from the Start Menu.
3. If the window stays blank, check **Connections → Windows desktop runtime**. A missing or broken WebView2 runtime must be repaired or reinstalled.
4. Restart Windows once. This clears a stuck WebView2 child process.

Do not reinstall the application as the first response; it will not repair a blocked WebView2 runtime and it risks unnecessary churn.

---

## 4. Cannot sign in

### The password is rejected

**Possible cause:** passwords are case-sensitive; the username is not. Caps Lock, a keyboard layout change or a trailing space can cause a failure.

**Safe steps:**

1. Retype the password slowly with Caps Lock off.
2. Check the username. The default owner username is `owner` unless it was changed during setup.
3. If another owner can sign in, ask them to reset your password from **Users → Reset password**.
4. If the only owner password is lost, do not delete the database. The account cannot be recovered from the interface; seek help from whoever maintains the installation, and keep a copy of the data folder first.

### The account is temporarily locked

**Possible cause:** five failed attempts within 15 minutes.

**Safe steps:** wait for the lockout window to pass, then try again once. Repeated attempts extend the delay. Do not create a new database to bypass the lock.

### The app shows **Set up Newsroom** again

**Possible cause:** Newsroom is using a different data folder, or the database was replaced or removed.

**Safe steps:**

1. Do not complete setup over the old data if you expect existing editions.
2. Check that `%LOCALAPPDATA%\ChannelPress\channel_press.db` exists.
3. If you recently changed `CHANNEL_PRESS_DATA_DIR` or moved the application, restore the previous data folder path.
4. Restore from a backup if necessary; see §15.

---

## 5. Window, background and shutdown problems

### A second launch does not open a second window

This is intentional. Newsroom is a single-window application. Starting it again focuses the existing window. If the window was hidden with **Continue in background**, launching again restores it.

If no window appears and Task Manager shows no **Newsroom** process, the application has exited. Start it normally.

### **Pause safely and exit** takes a long time

**Possible cause:** a model operation, media download or page render is still reaching a checkpoint.

**Safe steps:**

1. Wait. The window may remain open while the current operation finishes.
2. Do not end the process unless it has been unresponsive for several minutes.
3. If it must be ended, use Task Manager to end **Newsroom** and **NewsroomWorker**, then reopen. A queued job should be recoverable from **Jobs**.
4. Check **Jobs** after reopening. A job interrupted by shutdown may be `paused`, `resumable` or `failed_retryable`.

### The close dialog does not appear

The close choice is shown when you close the application window. If the window is already unresponsive, Windows may close it without showing the dialog. Reopen and check **Jobs** before starting new work.

### The application is running in the background and I forgot

Open Task Manager and look for **Newsroom**. Or launch Newsroom again; it focuses the hidden window. Use **Pause safely and exit** when you are finished for the day, especially before backup, uninstall or Windows updates.

---

## 6. Telegram connection problems

> **Scope:** the installed build exposes the Telegram connection screens, but the API id/hash are not configured by the installer. Live Telegram login and retrieval could not be verified for this documentation. The verified alternative is the rights-cleared JSON import on the source page.

### **Missing** or **API id/hash are not configured**

**Meaning:** this installation has no Telegram API application credentials, so it cannot open a live Telegram session.

**Safe steps:**

1. Continue with the **Import authorized content** path on the source page. It needs no Telegram account.
2. If live access is required, treat the API credentials as an owner-level configuration task outside the normal Newsroom screens. Do not paste API hashes, login codes or two-step passwords into a documentation request, chat or email.
3. Do not rename the channel or edit the database to make the warning disappear. The warning reflects a missing prerequisite, not a display bug.

### The login code does not arrive

**Possible cause:** the phone number is not in international format, the Telegram account is not reachable, or a code was requested too many times.

**Safe steps:**

1. Confirm the number includes the country code and no local trunk prefix, for example `+92…`.
2. Check that the Telegram account can receive messages on another device.
3. Wait for the login window to expire before requesting another code. Repeated requests can trigger a rate limit.
4. Use **Cancel login** and start again only after the wait.

### **FloodWait** or "too many attempts"

**Meaning:** Telegram has rate-limited the account.

**Safe steps:** stop retrying. Wait for the period stated by Telegram, then try once. Do not run the same request in a loop. If a collection was interrupted, the stored checkpoint may allow it to resume without re-fetching everything.

### The channel is private or inaccessible

**Possible cause:** the signed-in account is not a member, the handle changed, or the channel was deleted.

**Safe steps:**

1. Join the channel with the authorised account, or ask the owner to grant access.
2. Save the exact `@handle` or `t.me` address on the connection page and use **Test source connection**.
3. Confirm the rights declaration is active. Public read access is not permission to republish.
4. If the channel identity changed, stop and verify that you are pointing at the intended source before collecting.

### The session expired or was revoked

**Possible cause:** the local session is no longer valid, or the account signed out elsewhere.

**Safe steps:**

1. Sign in again on that channel's Telegram page.
2. If you intend to stop using the account, use **Sign out** on the Telegram page. That removes the local session through the application.
3. Do not delete `telegram.session` by hand while Newsroom is running. A session file behaves like a password.

### **Telegram session currently busy**

**Meaning:** a worker or another Newsroom process is using the session.

**Safe steps:** wait for the current collection or build to finish, then use **Test source connection** again. Do not start a second Newsroom instance. If the message persists with no job running, close Newsroom with **Pause safely and exit** and reopen it.

### Network restrictions and regional blocking

Telegram may be unreachable on some networks or in some regions. Newsroom does not include a built-in circumvention tool, and this manual does not recommend one. Use a network on which Telegram is legitimately available, or use the rights-cleared import path. Do not assume that a VPN or proxy is supported by the application; if you use one, it must be configured at the Windows network level and must not break local loopback access.

---

## 7. AI model problems

Newsroom runs models through a local Ollama service. The installer does not bundle Ollama or model weights.

### **Local AI runtime unavailable**

**Possible cause:** Ollama is not installed, is not running, or is listening on a different address.

**Safe steps:**

1. Confirm Ollama is installed and running on this PC. Newsroom will not start an Ollama service it does not own.
2. In Newsroom, open **Connections** or **Diagnostics** and read the **Local model** result.
3. Reopen **AI Models** after the service is available. The page should no longer show the red runtime notice.
4. If your organisation blocks local services, ask the administrator before changing firewall settings.

### A model download fails or stops

**Possible cause:** no internet, a registry rate limit, insufficient disk space, or the local Ollama service stopped.

**Safe steps:**

1. Check free disk space. The three profiles need roughly 2.68 GB, 3.32 GB or 6.55 GB for the model itself, plus temporary space.
2. Confirm the connection is stable and retry from **AI Models**.
3. If a transfer is running, use **Cancel transfer** before retrying; do not start a second download for the same profile.
4. If the page reports a digest mismatch, do not force the model into use. Remove and re-download the profile through **AI Models** when the service is healthy.
5. A failed download never changes an existing published edition.

### The model is installed but drafting refuses to start

**Possible cause:** the selected model has not passed the local test for its exact installed digest.

**Safe steps:**

1. Open **AI Models**.
2. Confirm the profile shows as installed and selected.
3. Choose **Test local model** and wait for the result.
4. If the test fails, read the message. A memory failure needs a smaller profile or more available memory; a schema failure may need a retry; a timeout may need a shorter issue.
5. Do not switch a real channel to "fixture" drafting to bypass the test. Fixture drafting is reserved for the demonstration channel.

### Out of memory, very slow drafts, or the machine becomes unresponsive

**Possible cause:** the model is larger than the available GPU or system memory, or a model and a large layout are running together.

**Safe steps:**

1. Use **Standard** or **Compact** rather than **Enhanced** on a 6 GB GPU or a 16 GB laptop.
2. Close other memory-heavy applications before a build.
3. Wait for the current build to finish before starting another.
4. Do not run a 150-post layout and a local model at the same time on a memory-constrained machine.
5. If the machine is unresponsive, wait for the checkpoint; if you must restart, recover the job from **Jobs** afterwards.

### AI output is poor or contains unsupported statements

This is an editorial issue, not a reason to trust the output more. Newsroom runs an independent evidence audit, but the audit cannot prove that a sentence is true. Read **Source evidence**, **Claims** and the **Audit** panel. Edit or hold the article. A model result is never a substitute for editorial verification.

---

## 8. Jobs, queue and build problems

### A build stays **Scheduled** or **Queued**

**Possible cause:** the background worker did not start, is already busy, or the machine blocked the worker process.

**Safe steps:**

1. Open **Jobs** and the job page. Read the stage and progress.
2. Wait a short time. A second build does not make the first one faster.
3. Check **Connections → Local model** if the job is a real-channel build; real-channel drafting requires a tested model.
4. If the job remains queued with no progress, use **Stop safely**, then **Continue from checkpoint**. If the job never started, **Retry failed job** or **Restart from beginning** may be available.
5. Do not create a second job for the same window. Newsroom reuses or refuses a duplicate window by design.

### A job seems stuck in a stage

**Possible cause:** a long model call, a large media download, a slow PDF render, or a stale worker.

**Safe steps:**

1. Read the progress line and the **Heartbeat** time. A stale heartbeat is different from a slow operation.
2. Wait for the watchdog message. Long operations should report that they are still working.
3. If the heartbeat is stale, use **Stop safely**, then **Continue from checkpoint**.
4. Use **Restart from beginning** only when you accept that the current draft state will be discarded.
5. Use **Cancel job** only when you want to abandon that job permanently; it cannot be undone, though its source checkpoint is retained for a later manual build.

### A job failed

**Safe steps:**

1. Read the error on the job page. The stage name tells you where it stopped: collection, classification, drafting, media, layout or release.
2. If the error mentions a missing rights declaration, fix the rights on the source page; do not retry blindly.
3. If the error mentions media, check whether the source file was deleted or withdrawn. Text can still render without an image.
4. If the error mentions the model, see §7.
5. If the error mentions layout or PDF, see §10.
6. After fixing the cause, use **Retry failed job**. Keep the job number for support.

### **Awaiting review** will not release

This is not a failure. The edition has open review items. Open the edition and read **Release held**. Resolve each item in the article's **Review items** panel, set the article status, then rebuild the preview and release again. A high-risk or unsupported claim always needs an editor.

### A build produced **no content**

A verified empty window is not a skip. It means no includable posts were found in that cutoff window. Check the source posts and the window boundaries. A post at exactly the cutoff belongs to the next issue.

### The machine restarted during a build

1. Start Newsroom and open **Jobs**.
2. A job interrupted by a restart may be `paused`, `resumable` or `failed_retryable`.
3. Use **Continue from checkpoint** if available, or **Retry failed job** after the cause is understood.
4. Do not create a duplicate build for the same window unless the existing job is terminal and you have read its result.

---

## 9. Review and release problems

### **Release version** is disabled

**Possible cause:** one or more blocking review items remain.

**Safe steps:** open each article in the edition, read **Review items**, choose **Approve item** or **Reject item**, and set the article status. Use **Rebuild preview only** after copy changes. Then return to the edition and try again.

### I edited a published edition and now see a new version

This is expected. A published edition is immutable. Saving an article from a published workspace creates a new working version, typically **Open working version N+1**. The published version stays frozen. Review the new draft, then release the new version when it is ready.

### The preview does not match the saved text

**Possible cause:** the preview is stale.

**Safe steps:** choose **Rebuild preview only** or **Save and Update Preview**. Wait for the preview state to become **current**. Do not release a version whose preview is stale.

### A correction needs to be visible to readers

Create a new working version, make the correction, rebuild the preview, and release the new version. Do not edit files in an old edition folder. The old version remains available for the audit trail.

---

## 10. PDF, preview and layout problems

### **The PDF renderer is unavailable** or the build cannot render

**Possible cause:** the bundled print browser is missing or cannot start, or the Windows runtime is damaged.

**Safe steps:**

1. Open **Connections** and check **Print browser** and **Paged.js renderer**.
2. Close and reopen Newsroom.
3. If the runtime or print browser remains unavailable, reinstall the same Newsroom release over the existing installation. Your data folder is separate and should remain.
4. If the problem persists, collect the job's log path and contact support.

### The preview PDF is blank, truncated or out of date

1. Choose **Rebuild preview only**.
2. If the pages are blank, inspect the job log and the edition's **Quality inspection** panel.
3. Check that the page size is **Tabloid 11 × 17 in** or **A3** as intended.
4. Do not release an edition with a blank page or an obvious clipping error.

### Text is clipped, overlapping or runs off the page

**Possible cause:** an unusually long headline, an unbroken URL, a long proper name, a wide table, or a theme metric that is too large.

**Safe steps:**

1. Shorten the headline or move detail into the deck.
2. Break long URLs or names in the copy where that is editorially acceptable.
3. Check the **Quality inspection** result for the failing page.
4. Do not reduce the body font below the allowed minimum just to force a fit.
5. Rebuild the preview and inspect at 100% zoom.

### Pages are underfull

Underfull pages are reported, not automatically a proof failure. A sparse page may be a legitimate short issue or a composition problem. Check the article mix, section ordering and page plan. Do not add filler or invented copy to fill space.

### The PDF is not 11 × 17 inches

Check **Branding → Paper size**. The tabloid PDF should be 11 × 17 inches (792 × 1224 points). If the geometry is wrong, correct the publication profile and rebuild. Do not release a wrongly sized PDF as a tabloid.

---

## 11. Images, media cache and fonts

### An image is missing from an article

**Possible cause:** the source media was not imported, was deleted at source, exceeded a size limit, failed its rights check, or was removed from the cache.

**Safe steps:**

1. Open the article's **Media** section and read the current selection.
2. Open the source post and check whether the media is still present and rights-cleared.
3. Re-import the media or choose another eligible source asset.
4. If the image is not essential, render the article text-only rather than using an unrelated picture.
5. Do not substitute a stock image and present it as source evidence.

### The Media cache page says files are protected

That is expected. Files used by a draft, an awaiting-review edition, a failed or running edition, an editor lock, a recent ingestion window or an incomplete published bundle are protected. Use the dry-run information before clearing anything.

The two clear buttons are destructive:

- **Clear unused media cache (safest)** removes only files the page identifies as safe to delete.
- **Clear all deletable media (including derivatives)** also removes derivatives.

Do not use either button while a build is running. If media is needed again, it may have to be downloaded or imported again. Source posts, captions, credits, rights records and audit events remain.

### Urdu or another script is in the wrong font

**Possible cause:** the chosen font family does not contain the required glyphs. Newsroom reports this in **Appearance → Print checks** instead of silently pretending the font supports the script.

**Safe steps:**

1. Choose a family with the required script coverage, or import a licensed font that has it.
2. Re-render the print proof and inspect the specimen.
3. Do not accept a fallback if the script is central to the page; check the actual PDF, not only the browser preview.

### An imported font cannot be deleted

A font used by a saved theme revision is intentionally protected so an archived design remains reproducible. Create a new theme without that font first, then remove it from the library if it is no longer referenced.

---

## 12. Source codes, QR labels and the evidence appendix

### No QR code is printed

**Possible cause:** the publication has no verified public evidence-page base URL, or the source code policy is **No printed codes**.

**Safe steps:**

1. Open **Branding** and read **Source code policy**.
2. Only set **Public evidence-page base URL** if you actually host the exported source bundles at that address.
3. Tick **I have verified that the public evidence-page base URL serves the exported source bundles** only after checking it.
4. If no public target exists, leave the truthful source appendix mode in place. Do not invent a URL or print a localhost address.

### A QR code points to the wrong page

The printed code is only as good as the public evidence base URL and the source record. Test the URL from another device before release. If it is wrong, correct the branding and rebuild; do not edit the published PDF by hand.

### The e-paper source appendix is incomplete

Open the edition's **E-paper** and check the **Sources** page. A source may have no public URL; the appendix then shows a message rather than a broken link. That is deliberate. If the underlying source record is missing, check the article's evidence.

---

## 13. Database busy, duplicate data and concurrency

### **Database temporarily busy. Your change was not saved.**

**Meaning:** another Newsroom process held the single SQLite writer for a moment. The request was not applied.

**Safe steps:**

1. Wait a few seconds and repeat the action.
2. Check **Jobs** for a long-running collection or build.
3. Do not run two Newsroom windows or two workers against the same data folder.
4. If the message repeats, close Newsroom with **Pause safely and exit**, reopen it, and try once. Do not delete `-wal` or `-shm` files.

### Two editions exist for the same window

A manual build with a different window or edition name is a legitimate separate edition. For an unexpected duplicate, record both edition numbers and inspect **Audit** before deleting anything. Do not delete a published edition to "clean up"; published editions are the archive.

### The same build is submitted twice

Newsroom should reuse the existing job or tell you that the window was already built. If you see a duplicate, open **Jobs** and identify which job is active. Do not cancel a running job merely because a second submission returned an error.

---

## 14. Schedules and unattended runs

### A schedule did not create an edition at the cutoff

**Possible cause:** the schedule is disabled, the day is not selected, no worker was running at the cutoff, or the edition is waiting for review.

**Safe steps:**

1. Open **Schedules** and check **Enabled**, the weekdays, **Cutoff**, **Release** and the timezone.
2. Check **Review required** and **Auto-release only clean, non-risk editions**. A held edition does not release itself.
3. Use **Run the latest cutoff now** to test the schedule deliberately.
4. Use **Enqueue missed cutoffs** to catch up missed windows.
5. Remember the installed product does not create a Windows scheduled task. If Newsroom was closed, no worker was running. Automatic unattended runs require a Windows task or another worker setup outside the normal interface; this was not verified for this documentation.

### The release time passed but nothing was published

The release timestamp is not an automatic publish command for a held edition. A build that contains high-risk or unsupported claims waits for an editor even when auto-release is configured. Review the edition and release it manually.

---

## 15. Backup, restore and recovery

### Make a backup before changing anything

1. Close Newsroom with **Pause safely and exit**. Confirm that the **Newsroom** process is gone in Task Manager.
2. In File Explorer, open `%LOCALAPPDATA%`.
3. Copy the entire `ChannelPress` folder to an external drive or another safe location.
4. Keep the copy's date in its name, for example `ChannelPress-backup-2026-10-31`.
5. If you also want to preserve the exact installed program version, copy `%LOCALAPPDATA%\Programs\Newsroom` separately. The program folder is replaceable; the data folder is not.

The data folder contains the database, media, imported fonts, theme proofs, logs and published output. Treat it as private. It may also contain a Telegram session; never share it or put it in cloud storage you do not control.

### Restore a backup

1. Close Newsroom and confirm that no **Newsroom** or **NewsroomWorker** process remains.
2. Rename the current `%LOCALAPPDATA%\ChannelPress` folder to `ChannelPress-before-restore` rather than deleting it.
3. Copy the backup folder into `%LOCALAPPDATA%` and rename it to `ChannelPress` if necessary.
4. Start Newsroom. Check **Dashboard**, **Editions** and a known PDF/e-paper.
5. If the restored database does not match the installed version, keep the original folder and seek help. Do not repeatedly overwrite the data folder.

Restore is a whole-folder operation. Do not merge individual database files from different dates.

### A published edition folder was moved or renamed

The edition may no longer appear correctly in Newsroom. Restore the folder to its original path from backup. Do not edit the manifest or checksum files.

### I need to recover an interrupted build

Use the job's **Continue from checkpoint** or **Retry failed job** actions before restoring an old database. A restore can lose newer work that is not in the backup.

---

## 16. Collecting information for support

### What to include

- Newsroom version (1.0.0 for this release) and the Windows version.
- The exact screen and button label where the problem occurred.
- The job number, stage and error text for a failed build.
- The exact status from **Connections** and **Diagnostics**.
- The last few lines of the relevant log file.
- Whether the problem happens with the fixture channel, an imported channel, or a live Telegram channel.

### Where the logs are

Open `%LOCALAPPDATA%\ChannelPress\logs`. Files are named by date, for example `desktop-20261031.log`, `web-20261031.log` and worker logs. A failed job page also shows the log folder path associated with that job.

### What never to send

Never send:

- `telegram.session` or any file with `session` in its name;
- `.env` or any file containing API credentials;
- `secret.key`;
- the database file, unless a trusted maintainer explicitly asks for a sanitised copy;
- private article text, source exports, phone numbers, channel member data or personal information.

Before sharing a log, remove Windows usernames, filesystem paths, channel handles, phone numbers and story text. A log may contain local paths even when it contains no credentials.

### When to stop and ask for help

Stop and seek help if:

- the database or data folder is missing;
- a restore fails;
- a published edition's files are damaged;
- a Telegram session may have been exposed;
- the application repeatedly fails at startup after a restart;
- you are asked to delete data to "fix" an error that is not explained.

---

## 17. Frequently asked questions

**Do I need an internet connection to use Newsroom?**  
No for local editing, viewing existing editions, and the fixture workflow. Yes for installing WebView2 if it is missing, downloading models, live Telegram access and optional web research.

**Does Newsroom upload my articles or sources to a cloud service?**  
The packaged application is designed to keep data local. AI inference is local through Ollama. Optional research providers, if configured, make external requests. The verified default workflow does not send source content to an online model.

**Can I run two copies of Newsroom?**  
No. It is a single-window, single-instance application so that the database and Telegram session are not used by two processes at once.

**Can I use Newsroom without AI?**  
The fixture demonstration can build without a model. A real channel's drafting requires a local Ollama model that has passed the local test in **AI Models**. There is no built-in cloud AI fallback.

**Can I publish automatically to a website or social network?**  
No. **Release version** freezes a local edition. It does not upload it anywhere.

**Can I edit an edition after release?**  
Not the released version. Open a new working version, make the change, rebuild the preview and release the new version. The old version remains immutable.

**Where is the PDF?**  
Use **Published PDF** on the edition, or open `%LOCALAPPDATA%\ChannelPress\output\<publication>\<edition>\` in File Explorer.

**Can I read the e-paper without Newsroom?**  
Yes. The e-paper bundle is self-contained. Open its `index.html` from the edition folder. The in-app **E-paper** button opens the same bundle.

**Does a schedule run while Newsroom is closed?**  
Not by itself in the installed product. A schedule defines the cutoff and release times and can be run manually. Unattended operation depends on a Windows task or worker setup that the installer does not create.

**Why is my edition **Awaiting review**?**  
The build completed, but editorial review items remain. Open the edition, resolve the items in each article, rebuild the preview and release it.

**Can I delete a published edition?**  
Do not delete published editions. They are the archive and their checksums and provenance are part of the record. If disk space is a problem, archive the whole edition folder to external storage first.

**Is my Telegram session a password?**  
Yes. Anyone with the session file can act as that account until it is revoked. Keep the data folder private and use **Sign out** when you stop using an account.
