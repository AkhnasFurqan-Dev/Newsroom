# Newsroom — Installation & Maintenance

**Product:** Newsroom 1.0.0 for Windows  
**Audience:** The Windows user who owns and maintains the installation  
**Last verified against:** `NewsroomSetup.exe` 1.0.0 (288,395,624 bytes) and the packaged build of 31 October 2026

This manual covers installation, updates, file locations, backup, restore, uninstall, disk use, background operation, networking and local-data privacy. It uses Windows interface conventions wherever possible. It does not require Python, a terminal or repository access.

> **Scope note:** the installer and package layout were inspected directly, and the packaged application was run against a disposable profile. A full clean-machine update and uninstall re-test was not repeated for this documentation. Where a behaviour was not exercised, this manual says so.

---

## 1. What you receive

| Item | Details |
|---|---|
| Installer | `NewsroomSetup.exe`, version 1.0.0, 288,395,624 bytes |
| Installed application | `Newsroom.exe` and its support files |
| Default install folder | `%LOCALAPPDATA%\Programs\Newsroom` |
| Approximate installed size | About 831 MB |
| Shortcuts | **Newsroom** in the Start Menu; desktop shortcut optional |
| Separately installed | Microsoft Edge WebView2 Runtime, if missing |
| Not bundled | Ollama, AI model weights, FFmpeg |

The installer is per-user. It installs into the current Windows account's local application folder and normally does not request administrator rights. The application is 64-bit.

The installer does **not** contain a cloud account, subscription, product key or online activation step. It also does not install a Windows service, a scheduled task or an automatic startup entry.

### 1.1 Components needed at run time

| Component | Status | Why it matters |
|---|---|---|
| Windows 10 or Windows 11, 64-bit | Required | The packaged application targets 64-bit Windows. |
| Microsoft Edge WebView2 Runtime | Required; installed by setup if missing | Renders the application window. |
| Bundled Chromium print browser | Included in the package | Renders the print PDF. |
| Paged.js renderer and PyMuPDF | Included | Layout and PDF geometry inspection. |
| fontTools | Included | Installed-font discovery and font validation. |
| OpenCV | Included | Video still extraction fallback. |
| FFmpeg | Optional | Better video still extraction; OpenCV is used when FFmpeg is absent. |
| Ollama | Optional, separate | Serves local Qwen3.5 models. |
| AI model weights | Optional, separate | 2.68–6.55 GB per reviewed profile. |
| Internet access | Optional for editing; needed for WebView2 bootstrap, model downloads, live Telegram and external research | Local editing and viewing already-downloaded editions work offline. |

The fixture demonstration and the PDF/e-paper workflow do not require Ollama or a model.

---

## 2. Install Newsroom

1. Double-click `NewsroomSetup.exe`.
2. If Windows SmartScreen warns about an unrecognised publisher, choose **More info** and **Run anyway** only if you obtained the file from the owner or the approved release location. This build is not code-signed.
3. Choose **Next** on the welcome page.
4. Keep `%LOCALAPPDATA%\Programs\Newsroom` unless you have a specific reason to change it. The folder is private to your Windows account.
5. Leave **Create a desktop shortcut** selected if you want one. The Start Menu shortcut is always created.
6. Choose **Install**.
7. If WebView2 is missing, setup runs Microsoft's signed bootstrapper silently. This needs an internet connection and can take a few minutes.
8. Leave **Launch Newsroom** selected and choose **Finish**.

**What you should see:** one window titled **Newsroom**. There is no terminal window, browser address bar or separate worker console.

### 2.1 If setup cannot install WebView2

Setup stops with a message that the runtime could not be installed or was not detected. Check:

- the internet connection and any proxy;
- whether Microsoft Edge or another WebView2 application can update;
- whether an organisation policy blocks the runtime.

Install the current Microsoft Edge WebView2 Evergreen Runtime manually, then run `NewsroomSetup.exe` again. Newsroom cannot render its window without it.

### 2.2 First run

The first launch shows **Set up Newsroom**. Create the owner account, choose a display name and a password of at least 12 characters, and decide whether to create the synthetic fixture channel. The fixture is for practice and is labelled synthetic.

Keep the owner password safe. There is no email-based reset in the installed application. An owner can reset another user's password from **Users**; losing the only owner password requires help from whoever maintains the installation.

---

## 3. Where files are stored

`%LOCALAPPDATA%` is usually `C:\Users\<your Windows user>\AppData\Local`. Paste the path into File Explorer's address bar.

| Item | Default location | Notes |
|---|---|---|
| Program files | `%LOCALAPPDATA%\Programs\Newsroom` | Replaced on update. |
| Your data | `%LOCALAPPDATA%\ChannelPress` | Database, media, settings, fonts and sessions. |
| Published editions | `%LOCALAPPDATA%\ChannelPress\output` | One folder per publication and edition. |
| Logs | `%LOCALAPPDATA%\ChannelPress\logs` | Desktop and worker logs. |
| Database | `%LOCALAPPDATA%\ChannelPress\channel_press.db` | SQLite database in WAL mode. |
| Telegram session | Under the data folder | Created only after a successful live login. Treat it like a password. |
| Imported fonts | Under the data folder | Stored by checksum. |
| Ollama models | Managed by Ollama, outside the Newsroom data folder | Not counted in the Newsroom data size. |

The folder name `ChannelPress` is retained for compatibility with the application's former name. It is the current data folder; do not rename it.

The edition workspace's **Provenance** panel shows the exact folder for a released edition. A typical edition folder contains the PDF, page proofs, the e-paper bundle, a manifest and checksums.

### 3.1 Do not edit published files

A released edition is immutable. Renaming or editing files inside its folder breaks the manifest and checksums and destroys the audit trail. Make corrections through a new working version, not through File Explorer.

---

## 4. Update an existing installation

Newsroom is the current name of the application formerly distributed as **Channel Press**. The installer recognises an existing compatible installation and updates it in place.

### 4.1 Before updating

1. Close Newsroom with **Pause safely and exit**.
2. Confirm that no **Newsroom** or **NewsroomWorker** process remains in Task Manager.
3. Back up `%LOCALAPPDATA%\ChannelPress` as described in section 6.
4. Note the current version from **Diagnostics → About Newsroom** or from the installer.

### 4.2 Run the update

1. Run the newer `NewsroomSetup.exe`.
2. Setup detects the existing installation and shows a confirmation:

   > An existing compatible installation was found (version …). This setup updates application files in place. Your personal data, Telegram sessions, settings and editions are stored separately and will not be removed. Continue?

3. Choose **Yes**.
4. Keep the same install folder.
5. Finish setup and launch Newsroom.

**What you should see:** the existing data, accounts, sources, drafts and editions are still present. The Start Menu and desktop shortcuts point to **Newsroom**, and the old `Channel Press` shortcuts and executable names are removed.

Do not uninstall the old application first. The installer's upgrade path is designed to preserve the data folder, and uninstalling adds risk without benefit.

### 4.3 If the update fails

- Read the setup message before closing it.
- Confirm Newsroom is not running.
- Confirm there is enough disk space for the new files plus the existing data.
- Run the same installer again; it is safe to repeat.
- Do not delete the data folder to "start clean". Restore from backup only if the data is actually damaged.

A clean-machine update re-test was not performed for this documentation. The upgrade prompt and install behaviour were inspected in the installer configuration and the release report.

---

## 5. Repair

There is no separate **Repair** entry in the installed application. To repair a damaged program installation:

1. Close Newsroom with **Pause safely and exit**.
2. Run the same `NewsroomSetup.exe` over the existing installation.
3. Choose **Yes** when setup offers to update the files in place.

This replaces program files but does not replace your data. If the window is blank because WebView2 is broken, repair WebView2 first; reinstalling Newsroom will not fix a blocked runtime.

---

## 6. Backup

There is no in-app backup button. The supported manual method is to copy the whole data folder while Newsroom is closed.

### 6.1 What to back up

Back up the entire `%LOCALAPPDATA%\ChannelPress` folder. It contains more than the database:

- `channel_press.db` and its WAL/SHM files;
- source media and derivatives;
- published editions and e-paper bundles;
- imported fonts;
- settings and any Telegram session;
- logs.

Copying only the `.db` file is not a complete backup.

### 6.2 A safe backup procedure

1. In Newsroom, choose **Pause safely and exit** and wait for the window to close.
2. Open Task Manager and confirm there is no **Newsroom** or **NewsroomWorker** process.
3. Open `%LOCALAPPDATA%` in File Explorer.
4. Right-click the `ChannelPress` folder and choose **Copy**.
5. Paste it to a different drive or a verified backup location.
6. Rename the copy with the date, for example `ChannelPress-backup-2026-10-31`.
7. If the backup tool offers verification, run it. Otherwise open the copy and confirm that `channel_press.db` and `output` are present.

Do not back up while a build is running. A copied WAL database can be inconsistent if the application is still writing.

### 6.3 How often

- Before every application update.
- Before a Windows feature update.
- Before a large or high-value edition.
- On a regular schedule that matches how much work you can afford to lose.

Keep at least one backup on a different physical drive. A copy in the same folder is not a backup against disk failure.

---

## 7. Restore

Restore only when the current data is damaged or lost, and keep the damaged folder until the restore is verified.

1. Close Newsroom with **Pause safely and exit**.
2. Confirm that no Newsroom process is running.
3. Rename the current `%LOCALAPPDATA%\ChannelPress` folder to `ChannelPress-old-YYYYMMDD`. Do not delete it yet.
4. Copy the verified backup folder to `%LOCALAPPDATA%`.
5. Rename the copied folder to exactly `ChannelPress`.
6. Start Newsroom and sign in.
7. Check the Dashboard counts, open a recent edition, and open its **Published PDF** and **E-paper**.
8. Check **Jobs** for interrupted work before starting a new build.
9. Only after the restore is verified, remove the old folder when you are sure you no longer need it.

**Success check:** the expected newspaper, sources, editions and accounts are present, and a published edition opens. If the app shows **Set up Newsroom**, it is using a different data folder or the database was not restored to the right path.

Restore was not exercised end to end for this documentation. Test a restore on a disposable machine or profile before relying on it for a critical archive.

### 7.1 Restoring only an edition

If you need one edition rather than the whole archive, copy its folder from the backup's `output\<publication-slug>\` into the live output folder while Newsroom is closed. Keep the manifest and checksums together. Do not merge two different editions into one folder.

---

## 8. Uninstall

### 8.1 Standard uninstall

1. Close Newsroom with **Pause safely and exit**.
2. Open Windows **Settings → Apps → Installed apps**.
3. Find **Newsroom** and choose **Uninstall**.
4. Follow the uninstaller.

You can also use the uninstall entry in the Start Menu folder if present. The uninstaller removes the program files and shortcuts.

### 8.2 What happens to your data

The data folder `%LOCALAPPDATA%\ChannelPress` is outside the installation folder and is not part of the uninstall. Your database, media, editions, fonts and sessions are preserved by design.

If you intend to remove the data as well:

1. Make a backup first.
2. Confirm that you no longer need the archive.
3. Delete `%LOCALAPPDATA%\ChannelPress` manually in File Explorer.

Deleting the data folder cannot be undone. Do not do it to troubleshoot a startup error before reading **Troubleshooting & FAQ**.

### 8.3 Reinstall after uninstall

Install the same or a newer version. If the data folder is still present, the existing owner account and archive should be available at sign-in. If you removed the data folder, first run shows **Set up Newsroom** again.

A full uninstall/reinstall cycle was not repeated for this documentation.

---

## 9. Disk use and local models

### 9.1 Application and data

- Installed application: about 831 MB.
- Data: depends on the archive, source media and editions. The Diagnostics page reports free space against a quota target, not the size of your archive.
- A single edition with images can add tens or hundreds of megabytes.

Check free space before a large build. The **Connections** and **Diagnostics** pages report free disk space in the data volume.

### 9.2 Models

The reviewed local models are separate downloads:

| Profile | Model | Download |
|---|---|---:|
| Standard | `qwen3.5:4b-q4_K_M` | 3.32 GB |
| Enhanced | `qwen3.5:9b-q4_K_M` | 6.55 GB |
| Compact | `qwen3.5:2b` | 2.68 GB |

Ollama stores the weights in its own location, outside `%LOCALAPPDATA%\ChannelPress`. Removing a model is an Ollama management task, not a Newsroom data operation. Newsroom does not bundle Ollama and will not start a service it does not own.

Allow temporary space for a download in addition to the final model size. A failed download does not change a published edition.

### 9.3 Media cache

**Media cache** shows a dry run of deletable files and a protected set. The protection list includes media referenced by active or awaiting-review editions, editor locks, provenance and rights records, source post records and uncertain files. Treat both clear buttons as destructive and back up the data folder first. The feature was not exercised for this documentation.

---

## 10. Starting, background operation and shutdown

### 10.1 Starting

Launch **Newsroom** from the Start Menu or the desktop shortcut. The application starts its local service and opens one window. The first window may take a few seconds while the database opens.

Only one window can run at a time. Launching again focuses the existing window rather than starting a second copy. This protects the database and any Telegram session.

### 10.2 Background work

A build starts a one-shot worker outside the browser request. If you hide the window with **Continue in background**, the worker continues. The worker is still a process on this computer; it is not a cloud job.

The installer does not create:

- a Windows service;
- a scheduled task;
- an automatic startup entry;
- an unattended overnight build.

Schedules in the app define cutoff and release policy and support **Run the latest cutoff now** and **Enqueue missed cutoffs**. Automatic execution while the application is closed was not verified and should not be relied on.

### 10.3 Safe shutdown

Close the window with the X and choose:

| Choice | Result |
|---|---|
| **Continue in background** | Hides the window; the local service and queued work continue. Launch again to restore the window. |
| **Pause safely and exit** | Waits for the worker and model operations to reach safe checkpoints, then stops the local service. |
| **Keep open** | Returns to the application. |

Use **Pause safely and exit** before backup, update, uninstall or a Windows restart. If it seems to hang, wait; a model call or page render may still be reaching a checkpoint. End the process only after several minutes of no response, then recover the job from **Jobs**.

### 10.4 After a restart

Open **Jobs** before starting new work. A job interrupted by a restart may be paused, resumable or retryable. Use **Continue from checkpoint** or **Retry failed job** after understanding the cause. Do not create a duplicate build for the same window.

---

## 11. Network, firewall and privacy

### 11.1 What needs the network

| Activity | Network needed |
|---|---|
| Install or update if WebView2 is missing | Yes, to Microsoft's bootstrapper. |
| Open and edit existing local editions | No. |
| Build from the fixture desk | No. |
| Build from a rights-cleared JSON import | No, once the file and media are local. |
| Draft with a local model | No internet, but the local Ollama service must be reachable. |
| Download or verify a model | Yes, from the verified Ollama registry. |
| Live Telegram login and retrieval | Yes, and API credentials must be configured. |
| External research providers | Yes, when a provider is configured and triggered. |
| View a published PDF or e-paper | No. |

The local service listens on loopback. If a firewall or endpoint-protection product blocks loopback or the worker process, the window may open but builds or previews fail. Allow the application on private/local connections rather than disabling the firewall.

### 11.2 Proxy and regional restrictions

Newsroom has no built-in proxy or circumvention tool. If the computer uses a proxy, configure it at the Windows or browser level so WebView2 and the model downloader can reach their services. Telegram may be unreachable on some networks or in some regions; use a network on which it is legitimately available or use the rights-cleared import path. Do not assume that a VPN is supported by the application.

### 11.3 Local data and privacy

- The database, media derivatives and published bundles stay under the local data and output folders.
- The footer states **Data stays on this computer.**
- There is no account, cloud sync or subscription screen in the inspected build.
- No telemetry or analytics setting is exposed in the application.
- Credentials are optional and stored outside Git.
- The Telegram session file behaves like a password. Anyone with a copy can act as that account until it is revoked. Sign out from the source connection page to remove it.
- Logs can contain local paths, job details and error text. Review a log before sending it to anyone.
- A published PDF or e-paper may contain source material, names and images. Treat the output with the same care as the reporting.

### 11.4 Multiple Windows accounts

The installer is per-user. Each Windows account has its own program folder and its own data folder. A second Windows user does not automatically see the first user's newspapers. To move an archive between Windows accounts, copy the data folder while Newsroom is closed, then place it in the second account's `%LOCALAPPDATA%`.

---

## 12. Maintenance schedule

A simple routine keeps the installation healthy:

| When | Task |
|---|---|
| Daily | Check **Connections** if something seems wrong; resolve review items before release. |
| Weekly | Confirm a current backup exists on a different drive. |
| Monthly | Check free disk space, review **Media cache** dry run, and read the **Audit** trail for unexpected changes. |
| Before every update | Close safely, back up the data folder, note the version. |
| After every update | Sign in, open a recent edition, open its PDF and e-paper, and check **Jobs**. |
| Quarterly | Test a restore on a disposable profile or machine. |
| When staff change | Disable unused accounts and reset shared credentials. |

---

## 13. Related manuals

- **Quick Start** — install and produce a first preview.
- **User Manual** — every verified screen and control.
- **Editorial & Publishing Guide** — review, release and corrections.
- **Troubleshooting & FAQ** — recovery when something fails.
- **Documentation Index** — the full document map.
