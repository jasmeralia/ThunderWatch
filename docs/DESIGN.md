# ThunderWatch design and implementation plan

Status: design stage. Nothing described here is implemented yet. ThunderWatch joins
GaleFling, StormFuse, and TempestTrace in the Storm Desktop Suite and reuses their
framework, dark theme, updater, packaging, and CI conventions. TempestTrace is the
primary template because it is the newest and smallest of the three.

## Goal and boundaries

ThunderWatch is a small PyQt6 tray app that starts when the user signs in, watches the
machine's public (WAN) IP address, and emails a configured recipient when that address
**actually changes**. The motivating use: run it on Rin's Windows PC so Morgan learns
promptly when Rin's home IP changes and the fail2ban allowlist needs updating. It is a
desktop counterpart to the `truenas-typhoon/ip-monitor` container script, without that
script's nginx-proxy-manager ACL integration.

In scope for the first release:

- Windows 10/11 x64 (primary target) and Linux amd64/arm64 desktops.
- Public IPv4 and IPv6 `/64` prefix monitoring. Both are on by default, because the
  fail2ban allowlist covers both address families. IPv6 can be turned off.
- SMTP email notification with TLS, a first-run setup wizard, a settings dialog, a
  status window, tray notifications, start at sign-in, and GitHub-Releases updates
  with an opt-in beta channel.

Out of scope for the first release: macOS packaging (keep the code portable, but do
not build or test it), editing fail2ban or any remote allowlist, notification
channels other than email (Discord, push, etc.), monitoring more than one network, and
LAN/interface address monitoring.

## Change detection rules

The core rule: **a failed lookup is never a change.** No network, DNS failure,
timeouts, HTTP errors, malformed bodies, and non-global addresses (private, CGNAT,
loopback, link-local, IPv4-mapped IPv6) all count as *failures*. A failure never
triggers an email and never overwrites the stored address.

- **Providers (HTTPS only).** Plain-text "what is my IP" endpoints, tried in order:
  - IPv4: `https://api.ipify.org`, `https://checkip.amazonaws.com`,
    `https://ipv4.icanhazip.com`.
  - IPv6: `https://api6.ipify.org`, `https://ipv6.icanhazip.com`.

  Use stdlib `urllib.request` with a 10 s timeout, a `ThunderWatch/<version>`
  User-Agent, and a response cap of 64 bytes. Validate with `ipaddress`. IPv4 must be
  a global `IPv4Address`. An IPv6 result must be a global `IPv6Address` with no
  `ipv4_mapped`, and is normalized to its `/64` network, as `ip-monitor.py` does. No
  third-party HTTP library.
- **Baseline.** The persisted state keeps `last_reported` per address family: the
  value the recipient was last told about.
- **Unchanged.** When the first provider that answers returns `last_reported`, the
  check passes. One provider is enough.
- **Candidate change.** When a provider returns a different valid address, it must be
  confirmed by a *second, independent* provider in the same check. Only two providers
  agreeing on the same new value makes a confirmed change. Disagreement or a failed
  second provider is *inconclusive*: no email, nothing stored, and a retry in 60 s.
  This blocks false alarms from a single misbehaving provider.
- **Confirmed change.** Record a history event, send the change email, and show a tray
  balloon. Update `last_reported` to the new value **only after the email is accepted
  by the SMTP server**. If sending fails, the change stays pending: the next check
  sends to the then-current address. If the address flaps back to the old value
  before an email gets through, nothing is sent, because the recipient's allowlist is
  still correct. The same effect as ip-monitor's write-state-only-on-success rule, but
  applied to email delivery.
- **No baseline yet.** On the first confirmed address after setup (normally captured by
  the wizard's test email), send a "monitoring started" email with the current
  address, so the recipient always knows the starting value.
- **IPv6 availability.** If IPv6 monitoring is enabled but the machine has no IPv6
  connectivity, that is a failure, not a change. Losing IPv6 never sends an email.
  Gaining it again only emails if the prefix differs from `last_reported`. Because
  IPv6 is on by default and many connections have none, IPv6 failures alone never
  raise the amber tray badge or a balloon. The status window shows "IPv6: not
  available" with the time it was last seen instead. The badge reflects IPv4 lookups
  only.

### Check scheduling

- First check runs 15 s after launch, giving sign-in networking time to come up.
- Regular interval: 10 minutes by default, configurable from 5 to 720 minutes.
- After a failure or an inconclusive result, retry after 1, 2, 4, then 8 minutes,
  capped at the regular interval. The first success restores the normal interval.
- Check immediately when `QNetworkInformation` reports reachability becoming online,
  and after a suspend/resume. Detect resume as a wall-clock gap of more than twice
  the interval between timer ticks.
- **Check IP Now** in the tray runs a check immediately and resets the timer.
- Network I/O and SMTP run on a worker `QThread`. The GUI thread never blocks.
  Checks never overlap: a request that arrives while one is running is coalesced.

## Notification email

- SMTP settings: host, port, security mode, username, password (or app password),
  From address (defaults to the username), and one or more recipients (comma
  separated, each validated).
- Security modes: **Auto** (implicit TLS on 465, STARTTLS otherwise), **SSL/TLS**, and
  **STARTTLS**. Plaintext SMTP authentication is not offered. A failed STARTTLS
  upgrade fails the send. Generalize GaleFling's `src/core/smtp_utils.py` (which
  already does the 465 → `SMTP_SSL` split), with a 30 s timeout, and return
  `(ok, message)` without raising.
- A **location label** (default: the computer's host name, e.g. "Rin's PC") appears in
  every subject so the recipient knows which network changed.
- Change subject: `ThunderWatch: <location> IPv4 changed to <new>`. Use `IPv6 prefix`
  in place of `IPv4` when the prefix changed, and combine both families in one email
  when both changed in the same check.
- Body (plain text): each changed family as `old -> new`, the detection time in local
  time and UTC, the two providers that confirmed it, the host name, the ThunderWatch
  version, and a reminder line saying the recipient may need to update
  allowlists. Started subject: `ThunderWatch: monitoring started for <location>
  (<ip>)`. Test subject: `ThunderWatch test from <location>`, with the current address
  in the body when known.
- If delivery keeps failing, the tray shows the danger badge, one balloon says email
  delivery is failing, and the status window shows the SMTP error text. Retries follow
  the check schedule. There is no separate retry storm.

## User flow

### Startup

1. Parse arguments: `--autostart` (launched at sign-in), `--smoke-test` (build the app
   objects, then exit 0; used by CI as in TempestTrace), `--show` (open the status
   window).
2. **Single instance.** Use a `QLocalServer` named per user. A second launch sends
   "show" to the running instance, which opens its status window, and then exits 0.
3. Check whether a configuration exists: `setup/complete` is true and the non-secret
   SMTP fields (host, port, username, From address, at least one recipient) are
   present and valid.
   - **No configuration:** show the setup wizard, whether launched by the user or at
     sign-in.
   - **Configuration present:** go straight to the tray with no window. Read the
     password lazily, only when sending. If the secret store is unavailable or the
     password is missing (for example, the keyring is still locked right after
     sign-in), keep monitoring, show the danger badge with "Email password
     unavailable", and retry reading it on each send. A secret-store timing problem
     never re-runs the wizard.
4. **Tray availability.** At sign-in the tray may not exist yet. Poll
   `QSystemTrayIcon.isSystemTrayAvailable()` for up to 60 s. If there is still no
   tray (for example GNOME without the AppIndicator extension), open the status
   window with a notice explaining that the window stands in for the tray, and keep
   monitoring. Closing the window then minimizes instead of hiding.

### Setup wizard

Use a `QWizard` with GaleFling's step rail (`src/gui/setup_wizard.py`:
`_StepRail`/`_StepRailItem`) and the shared theme.

1. **Welcome:** what ThunderWatch does, what it sends, and that it contacts public IP
   lookup services over HTTPS.
2. **Email server:** host, port, security mode, username, password, From address.
   Common-provider presets (Gmail, Outlook.com, Fastmail, Custom) fill in host, port,
   and security only.
3. **Recipient and location:** recipients and location label.
4. **Monitoring and startup:** check interval, **Also monitor the IPv6 prefix** (default
   on), **Start ThunderWatch when I sign in** (default on), and **Include beta
   updates** (default off).
5. **Test and finish:** look up and show the current IPv4 address and IPv6 prefix. Show
   "IPv6 not available on this network" when there is none, as information, not an
   error. Then **Send Test Email**.
   Finish is enabled after a successful test. A "Save without a successful test"
   checkbox allows finishing anyway (for example, setting it up offline). A
   successful test email that included the current IP sets `last_reported`, so no
   separate "monitoring started" email follows.

Canceling a first-run wizard asks "ThunderWatch can't monitor without email settings.
Quit?" and exits if confirmed. The wizard can be run again from Settings, pre-filled
with current values. Canceling a rerun keeps the existing configuration.

### Tray

- The icon is the suite icon with GaleFling-style corner badges (port the badge
  compositor from `src/gui/tray_icon.py`). No badge means healthy. A **warning
  (amber)** dot means IPv4 lookups have failed three or more times in a row (offline),
  and clears on the next success. A **danger (red)** dot means an email is pending or
  failing, or the password is unavailable.
- Tooltip: `ThunderWatch - <location>`, the current IPv4 (and IPv6 prefix if enabled),
  and the last check time and result.
- Menu, alphabetical with Quit pinned last after a separator (GaleFling's convention):
  About, Check for Updates, Check IP Now, Copy Current IP, Send Test Email, Settings…,
  Show Status, separator, Quit.
- Double-click opens the status window. Clicking a balloon opens the status window.

### Status window

A compact `QMainWindow` showing:

- The current IPv4 and IPv6 prefix with a copy button.
- The last check time, result, and providers used.
- The last reported values and when they were reported.
- Any pending notification and its last SMTP error.
- The next scheduled check.
- A history table of the last 50 events: changes, emails sent or failed, and
  summarized runs of failures ("lookups failing since 09:12 (14 attempts)" rather
  than one row per failure).

Buttons: Check IP Now, Send Test Email, Settings, Open Log Folder. Closing the window
hides it to the tray. The first time, a balloon says ThunderWatch is still running.

### Settings dialog

Tabs:

- **Email:** the wizard's fields plus Send Test Email.
- **Monitoring:** location label, interval, IPv6.
- **Startup:** start at sign-in.
- **Updates:** check automatically (default on), include beta updates (default off).
- **Run Setup Wizard Again**.

Saving re-applies the autostart registration and reschedules the timer. Changing
the recipient does not reset `last_reported`.

## Configuration, secrets, and state

- **Settings:** `QSettings("WindsOfStorm", "ThunderWatch")` (the same organization as
  TempestTrace) for every non-secret value. Keys: `setup/complete`, `smtp/host`,
  `smtp/port`, `smtp/security`, `smtp/username`, `smtp/from`, `smtp/recipients`,
  `monitor/location`, `monitor/interval_minutes`, `monitor/ipv6`, `startup/autostart`,
  `updates/automatic`, `updates/include_beta`. Parse them into a frozen `Config`
  dataclass with validation, kept Qt-free behind a small settings-store protocol so it
  can be tested.
- **SMTP password:** stored with `keyring` (Windows Credential Manager; Secret Service
  on Linux) under service `ThunderWatch`, account `<username>@<host>`. If no usable
  keyring backend exists (a `fail` backend, or a Secret Service call error during the
  wizard), the wizard and settings dialog explain this. They then require an explicit
  "Store the password in a file only my account can read" checkbox before writing it
  to `<AppConfigLocation>/smtp-password`: mode `0600` on Linux, under the per-user
  profile on Windows. Never write the password to QSettings, logs, the state file,
  emails, or update requests.
- **State:** `<AppDataLocation>/state.json`, written atomically (temporary file plus
  `os.replace`) and versioned. Fields: `last_reported` per family plus a timestamp,
  `last_observed`, `last_check` (time, result, providers), `pending` (the change
  awaiting delivery, with its last error), and `history` (capped at 50). An invalid
  or unreadable state file degrades to empty state with a logged warning, as in
  `ip-monitor.py`'s `read_state`. The next confirmed address then sends a "monitoring
  started" email.
- **Logs:** a rotating log in `<AppDataLocation>/logs/` (1 MB × 5). IP addresses may be
  logged; secrets may not.

## Start at sign-in

Port GaleFling's `src/core/autostart.py` and add sandbox-aware branches. Every path
launches with `--autostart`.

- **Windows:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ThunderWatch`. The
  NSIS uninstaller removes this value.
- **Linux, DEB/RPM/AppImage/source:** `$XDG_CONFIG_HOME/autostart/thunderwatch.desktop`.
  For an AppImage, `Exec` uses `$APPIMAGE`, the persistent path, not the temporary
  mount.
- **Flatpak:** request it through the XDG Background portal
  (`org.freedesktop.portal.Background.RequestBackground` with `autostart=true` and
  `commandline=["thunderwatch", "--autostart"]`), called over `QtDBus`. A desktop
  file written inside the sandbox would not work. Report a portal denial in the
  settings dialog.
- **Snap:** declare `autostart: thunderwatch.desktop` on the app in `snapcraft.yaml`.
  The app writes the desktop file to `$SNAP_USER_DATA/.config/autostart/`.

## Updates and release channels

Port TempestTrace's `updater.py` and update dialog and adapt the asset names. The
behavior matches TempestTrace's "Updates and release channels" section:

- Check GitHub Releases without blocking: 30 s after startup, then every 24 hours,
  because the app is long-running. Settings toggles automatic checks. The tray's
  **Check for Updates** item runs an explicit check.
- Drafts are ignored. **Prereleases are ignored unless "Include beta updates" is
  enabled.** The highest compatible release with a matching OS/arch asset is offered.
  The dialog shows the current and offered versions, a stable/beta label, release
  notes, download size, and an explicit **Download and Update** button. An
  automatically found update shows a tray balloon first. Nothing installs silently.
- Downloads are verified against the release's `SHA256SUMS` and size. A failed update
  leaves the current version running.
- **Windows:** download the NSIS installer, quit ThunderWatch, and launch the
  installer detached. The installer's finish page relaunches the app.
- **Linux:** hand off to the matching package format with the same Flatpak, Snap,
  DEB, and RPM rules TempestTrace documents. The AppImage uses a verified
  replace-after-exit with rollback.
- The version is embedded from the CI tag (`APP_VERSION` in the PyInstaller spec, as in
  TempestTrace). The source tree's `0.0.0` never counts as an installed release.

## Architecture and toolchain

- Python 3.14, PyQt6, `keyring`, and the stdlib for HTTP and SMTP. `src/` layout with
  package `thunderwatch`, ruff (TempestTrace's rule set, line length 100, double
  quotes), mypy `--strict`, pytest with pytest-cov, and PyInstaller 6.x pinned as in
  TempestTrace.
- Keep the core Qt-free so it can be tested with fakes:

  | Module | Responsibility |
  | --- | --- |
  | `ipcheck.py` | Provider list, fetch and validate, the two-provider confirmation. Takes an injectable `fetch(url) -> str` callable. |
  | `monitor.py` | Pure decision function: `(state, lookup results, now) -> (new state, actions)`. Actions: send change email, send started email, notify tray, schedule retry. |
  | `notifier.py` | Email composition and the SMTP send, with an injectable SMTP factory. |
  | `state.py` | Atomic JSON persistence and the history cap. |
  | `config.py` | `Config` dataclass, validation, settings-store protocol. |
  | `secrets.py` | keyring wrapper plus the opt-in file fallback. |
  | `autostart.py` | Per-platform sign-in registration. |
  | `updater.py` | Port from TempestTrace. |

- The Qt layer:

  | Module | Responsibility |
  | --- | --- |
  | `app.py` | Arguments, single instance, startup routing, `main()`. |
  | `worker.py` | `QThread` running checks and sends. |
  | `scheduler.py` | `QTimer`, backoff, network/resume triggers. |
  | `tray.py` | Tray icon and menu. |
  | `wizard.py` | Setup wizard. |
  | `settings_dialog.py` | Settings dialog. |
  | `status_window.py` | Status window. |
  | `update_dialog.py` | Update dialog. |
  | `theme.py`, `tokens.py` | Copied from TempestTrace, which adapted them from GaleFling. Identical palette and QSS. |
  | `logging_setup.py` | Rotating log configuration. |

- **Icon:** the shared Storm Desktop Suite phoenix. `resources/icons/thunderwatch.png`
  and `thunderwatch.ico` are byte-identical copies of the GaleFling, StormFuse, and
  TempestTrace icons. The tray uses the PNG with badges; Windows uses the ICO for the
  exe and installer.
- **Repository layout:**
  - `src/thunderwatch/`, `tests/`, `scripts/release_info.py`.
  - `build/ThunderWatch.spec`, `build/installer.nsi`.
  - `packaging/linux/` (`build-packages.sh`, `.desktop`, Flatpak manifest,
    `snapcraft.yaml`, README).
  - `resources/icons/`, `tools/screenshots/` (README and wizard-step generators),
    `docs/` (including `docs/images/` and `docs/SETUP_WIZARD.md`).

  The tooling mirrors TempestTrace's: `Makefile` targets `deps`, `lint`, `lintfix`,
  `test`, `run`, and `screenshots`, plus `pyproject.toml`, `requirements-dev.txt`,
  `codecov.yml` (80% project and patch, `tests/**` ignored), and `.gitignore`.
- **Windows packaging:** a PyInstaller one-file windowed exe and a per-user NSIS
  installer (`$LOCALAPPDATA\Programs\ThunderWatch`, `RequestExecutionLevel user`).
  Like TempestTrace's, it kills a running `ThunderWatch.exe` before installing or
  uninstalling. It offers "Launch ThunderWatch" on its finish page, and the
  uninstaller also removes the HKCU Run value.
- **Linux packaging:** TempestTrace's matrix (DEB, RPM, AppImage, Flatpak, Snap × amd64
  and arm64) with app ID `io.github.jasmeralia.ThunderWatch`.
  - The Flatpak's only permissions: `--share=network`, `--socket=wayland`,
    `--socket=fallback-x11`, `--talk-name=org.kde.StatusNotifierWatcher`, and
    `--talk-name=org.freedesktop.secrets`. No filesystem access.
  - Snap plugs: `network`, `desktop`, `wayland`, `x11`, and `password-manager-service`.
    The release notes document `sudo snap connect
    thunderwatch:password-manager-service`.

## README screenshots

Use the same approach as the other suite apps: capture screenshots from the real Qt
widgets, rendered offscreen with synthetic data, and embed them in the README.

- **`tools/screenshots/generate_readme_screenshots.py`:** modeled on TempestTrace's and
  StormFuse's generators. Before importing Qt or app modules, it points `HOME`,
  `XDG_CONFIG_HOME`, `APPDATA`, and `LOCALAPPDATA` at a temporary scratch directory
  and sets `QT_QPA_PLATFORM=offscreen`. It also redirects `QSettings` to an INI file
  in that directory and installs an in-memory `keyring` backend, so the user's real
  settings, keyring, and state are never read or written. It removes the scratch
  directory afterwards.
  - **Synthetic data only.** The location label is "Example Home", the SMTP account is
    `alerts@example.com`, and the recipient is `you@example.com`. The password field
    shows only its mask. IPs come from the documentation ranges (`203.0.113.0/24`,
    `198.51.100.0/24`, `2001:db8::/32`). Timestamps are fixed so reruns produce
    stable images.
  - The generator fills the UI's state and history models directly instead of running
    lookups, because `ipcheck` correctly rejects documentation addresses as
    non-global. It never contacts IP providers, SMTP, or GitHub, and runs with
    updates disabled, as TempestTrace does.
  - It writes to `docs/images/`:
    - `status-window.png`: healthy, with a history that includes a confirmed change
      and its sent email.
    - `status-pending.png`: a change whose email is waiting to be sent, with an SMTP
      error shown.
    - `tray-menu.png`: the tray context menu, captured with `QMenu.grab()`.
    - `tray-states.png`: the icon in its healthy, amber, and red states, side by side
      at an enlarged size.
    - `settings-dialog.png`: the Email tab.
    - `setup-wizard.png`: the Email server step.
- **`tools/screenshots/generate_wizard_step_screenshots.py`:** GaleFling's pattern.
  It captures one PNG per wizard page, in order, into `docs/images/wizard-steps/`
  (`01-welcome.png` … `05-test-and-finish.png`). The last step shows a successful
  test email and the current IPv4 and IPv6 prefix. These images feed
  `docs/SETUP_WIZARD.md`, a step-by-step walkthrough linked from the README.
- **`make screenshots`** (depends on `deps`) runs both generators with
  `QT_QPA_PLATFORM=offscreen`. `make lint` runs ruff over `tools/screenshots/` as
  well.
- **README "Screenshots" section:** says the images come from the real interface with
  synthetic data and no real addresses or credentials. It embeds the status window,
  tray menu and states, settings, and wizard images with a one-line caption each,
  links to `docs/SETUP_WIZARD.md`, and ends with "Regenerate with `make screenshots`
  after UI changes."
- **No silent rot:** a pytest test imports both generators and runs their capture
  functions into `tmp_path`. It checks that every expected PNG exists and is
  non-empty, and that the real `QSettings` and keyring were never touched. A UI
  refactor that breaks screenshot capture therefore fails CI.
- A PR that visibly changes the status window, tray menu, settings, or wizard must
  regenerate and commit the affected images in the same PR.

## Repository and CI configuration

Applied to `jasmeralia/ThunderWatch` to match the rest of the suite.

**Repository settings:**

- The default branch is renamed `main` → `master`.
- Squash merge only: merge commits and rebase merges are disabled, and the squash
  title/message is `COMMIT_OR_PR_TITLE` / `COMMIT_MESSAGES`.
- Auto-merge, delete branch on merge, and "always suggest updating PR branches" are
  enabled.
- Dependabot vulnerability alerts and Dependabot security updates are enabled. Secret
  scanning and push protection were already on.

**`master` branch protection** (classic, as on GaleFling/StormFuse/TempestTrace):

- Pull request required, 0 approvals.
- `enforce_admins` on.
- **Required conversation resolution on**, so unresolved review comments block merges.
- Strict (up-to-date) status checks.
- No force pushes, no deletion.
- Required contexts `Lint & Test`, `codecov/project`, and `codecov/patch` are added
  in the PR that introduces the CI workflow. Before then, nothing reports them and
  they would block every merge.

**Rulesets:**

- **Copilot PR reviews:** active on `~ALL` branches, `review_on_push: true`, drafts
  excluded, with Dependabot (integration 29110) exempt. This is the same ruleset
  GaleFling and StormFuse use (TempestTrace's is disabled). Mandatory here. Per
  the global workflow, a pending Copilot review blocks merging even when the checks
  are green.

**Files:**

- `.github/dependabot.yml`: copy TempestTrace's (`pip` and `github-actions`, weekly
  Monday 09:00 America/Los_Angeles, grouped, `target-branch: master`, reviewer
  `jasmeralia`).
- `.github/workflows/dependabot-auto-merge.yml`: copy TempestTrace's (`gh pr merge
  --auto --squash` with `DEPENDABOT_MERGE_TOKEN`). The token must be added as a
  **Dependabot** secret (Settings → Secrets and variables → Dependabot), not an
  Actions secret, so the merge triggers release CI.
- `.github/CODEOWNERS`: `* @jasmeralia`.
- `.github/workflows/release.yml`: TempestTrace's workflow with names swapped. It
  covers:
  - `resolve-release` (`scripts/release_info.py`, `v0.1.x` patch series starting at
    `v0.1.0`).
  - `Lint & Test` with Qt system libraries, pytest-cov `coverage.xml`, and
    `junit.xml` uploaded to Codecov via OIDC.
  - Tag creation, the Windows exe and NSIS build with silent install, smoke test, and
    uninstall smoke test.
  - The reusable `linux-packages.yml`.
  - `publish-release`. It verifies all 11 assets plus `SHA256SUMS`, then runs `gh
    release create --prerelease --generate-notes` and never changes an existing
    release's status or assets.

  Every master merge therefore produces a **prerelease**. Morgan promotes a validated
  prerelease to stable by hand.
- `.github/workflows/linux-packages.yml`: TempestTrace's workflow minus the OBS and
  Dropbox fixture steps. For each format it installs, runs `--smoke-test`, and
  uninstalls. Flatpak adds a permissions assertion (network, StatusNotifierWatcher,
  secrets, and *no* filesystem grants). Under `xvfb` with a fake
  `StatusNotifierWatcher`, it also checks that a preconfigured instance starts
  without opening a window.
- **Codecov:** the repo must be active in Codecov for OIDC uploads. Confirm this when
  CI lands, before adding the required contexts.

## Test-driven implementation

Write a failing synthetic test first for every behavior. Tests never hit real IP
providers or SMTP servers.

- **`ipcheck`:**
  - Every failure class is a failure, never a change: timeout, `URLError`, HTTP 5xx,
    an oversized body, garbage text, and private, CGNAT, loopback, and IPv4-mapped
    addresses.
  - Provider fallback order.
  - Two-provider confirmation, the disagreement and second-failure inconclusive
    cases, and `/64` normalization.
- **`monitor`:**
  - An unchanged address.
  - A confirmed change, both with email success (baseline advances) and email failure
    (baseline holds and the change stays pending).
  - A flap back to the old value while pending (nothing is sent, pending clears).
  - First-baseline "started" email.
  - IPv6 loss and regain, and IPv6-only failures never setting the warning state.
  - Combined v4 and v6 changes in one email.
  - Backoff progression and reset.
- **`notifier`:** subject and body content per email type, and security-mode
  selection (Auto/465 → `SMTP_SSL`, otherwise STARTTLS). A STARTTLS failure surfaces
  as an error. The password never appears in the returned error text.
- **`state`:** atomic write, corrupt or unknown-version files degrade to empty, and the
  history cap.
- **`config` and `secrets`:** validation, the completeness rule, the keyring
  round-trip with a fake backend, and the fallback file only after explicit opt-in
  with `0600` on Linux.
- **`autostart`:** Windows registry calls (faked `winreg`), the desktop-file
  contents and `Exec` quoting, AppImage `$APPIMAGE`, the Flatpak portal call, and the
  Snap path.
- **Qt** (offscreen): startup routing, which shows the wizard with no config and only
  the tray with a complete config. Also: wizard page validation and the finish gate,
  tray menu order and badge states, single-instance "show" handoff, the
  status-window close-to-tray behavior, and the settings save re-registering
  autostart.
- **`release_info.py`:** port TempestTrace's tests.

## Delivery stages

1. **Repository setup:** apply the settings and rulesets above and merge this design
   doc.
2. **Scaffold and CI:** `pyproject.toml`, `Makefile`, `requirements-dev.txt`,
   `.gitignore`, `codecov.yml`, dependabot files, CODEOWNERS, icon, and a `Lint &
   Test`-only `release.yml`. Once Codecov reports, add the three required contexts to
   branch protection.
3. **Core:** `ipcheck`, `monitor`, `state`, `notifier`, `config`, `secrets`, test first.
4. **Qt app:** theme, tray, scheduler and worker, wizard, settings dialog, status
   window, single instance, autostart. Add the screenshot generators and their pytest
   guard, then commit the first `docs/images/` set, the README "Screenshots" section,
   and `docs/SETUP_WIZARD.md`.
5. **Updater:** port TempestTrace's updater and update dialog, with beta-channel
   gating.
6. **Packaging and release:** PyInstaller spec, NSIS, Linux packages, the full release
   workflow publishing prereleases. Regenerate the screenshots if the UI changed.
7. **Hands-on validation:** install the first prerelease on Rin's Windows PC.
   Confirm start at sign-in goes straight to the tray, the test email arrives, and a
   simulated change emails correctly (via a debug override of the provider list). Then
   promote the prerelease to stable.

## Acceptance checks

- A fresh install with no configuration opens the wizard. After finishing the wizard,
  later launches and sign-ins go straight to the tray with no window.
- Unplugging the network for any length of time sends no email and only shows the
  amber badge. Reconnecting with the same IP sends nothing.
- A real IP change (simulated in tests and in the debug build) sends exactly one email
  per change, confirmed by two providers, containing the old and new values.
- If SMTP is down during a change, the email goes out on a later check, and the
  baseline only advances after delivery.
- With beta updates off, the updater never offers a prerelease. With them on, it
  offers the newest prerelease.
- Every master merge publishes a prerelease with the Windows installer, all 10 Linux
  packages, and `SHA256SUMS`, after the smoke tests pass.
- `make screenshots` regenerates every README and wizard-step image offscreen from
  synthetic data, without touching real settings, the keyring, or the network. The
  README embeds those images.
- PRs cannot merge with unresolved review threads, a pending Copilot review, or a
  failing `Lint & Test`, `codecov/project`, or `codecov/patch`.

## Decisions to confirm during implementation

- The default check interval is **10 minutes** (ip-monitor uses hourly). It is
  configurable from 5 to 720 minutes.
- If no OS keyring is usable, an opt-in owner-only password file is the fallback. The
  alternative is to refuse to save, which would block Linux desktops without a Secret
  Service.
