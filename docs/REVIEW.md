# Adversarial review

Review date: 2026-10-06  
Reviewed revision: `8864777` (`feat/implement-thunderwatch`)  
Reviewers: Claude Opus 5.5 High and Cursor Grok 4.7 High

The reviews were read-only. Findings are consolidated below; severity and reviewer
attribution are retained where the reviews differed. Critical, High, and Medium findings
listed as fixed have regression tests and implementation fixes. Low findings remain
open. Verification after the fixes: `make lint` passed; `make test` passed with 129 tests
and 82% total coverage.

## Critical and High findings

1. **Critical — Qt worker lifetime could prevent checks and abort the process** (Opus).
   `Scheduler.run()` created local `QThread` and worker objects without retaining the
   worker; Opus reproduced a run that performed no state/check work and exited with
   status 134 when the thread was destroyed. Test-email and update paths also needed
   retained worker references. **Status: fixed.** Scheduler and app objects retain
   workers through thread completion; duplicate app worker starts are guarded.

2. **High — unexpected check exceptions could stop monitoring** (Opus and Grok).
   An exception from lookup or persistence could escape the worker, skip its finished
   signal, and prevent scheduler recovery. **Status: fixed.** The check worker logs the
   exception, records a failed check when possible, emits a retry result, and always
   completes its signal path.

3. **High — QSettings string values such as `"false"` parsed as true** (both).
   `bool("false")` evaluates to true, potentially re-enabling IPv6, autostart, or
   automatic/beta updates after restart. **Status: fixed.** Configuration uses explicit
   string/number/boolean parsing, and Settings uses the parsed configuration.

4. **High — SMTP TLS did not explicitly verify certificates** (Opus).
   SMTP SSL and STARTTLS used implicit defaults that the reviewer observed without
   hostname/certificate verification. **Status: fixed.** Both transport modes now use
   `ssl.create_default_context()`.

5. **High — outbound messages lacked standard headers** (both).
   SMTP envelope arguments did not add `From` or `To` message headers; Opus also noted
   missing `Date` and `Message-ID`. **Status: fixed.** The transport adds all four
   headers before sending.

6. **High — an initial IPv6 result could hide a real IPv4 change** (Grok).
   When IPv4 had a baseline but IPv6 did not, a simultaneous IPv4 change and first IPv6
   observation was classified as “monitoring started,” obscuring the IPv4 change.
   **Status: fixed.** A started notification is now selected only when every candidate
   family lacks a prior baseline.

7. **High (Grok) / Medium (Opus) — local-server failure could start duplicate monitors**
   (both). A failed `listen()` was ignored and a stale socket was not cleaned up.
   **Status: fixed.** Startup checks for a live instance, removes a stale socket, retries
   listen, and exits if it cannot claim the server name.

8. **High (Grok) / Medium (Opus) — rerunning setup reset preferences** (both).
   Security mode, interval, IPv6, autostart, and beta settings could revert to widget
   defaults; finishing setup also skipped applying autostart. **Status: fixed.** The
   wizard now loads all saved preferences and reapplies autostart after saving.

## Medium findings

9. **SMTP account changes can leave the password unavailable** (Opus). The secret is
   keyed by username and host; changing either without entering a replacement password
   can fail on the next notification. **Status: fixed.** Settings and setup reruns now
   require a password before saving a changed SMTP identity.

10. **Saving Settings can create another recurring update-check chain** (Opus). Each
   accepted save with automatic updates schedules another delayed check. **Status:
   fixed.** One retained single-shot timer is stopped or armed as the setting changes,
   preventing duplicate recurring chains.

11. **Pending IPv6 changes can remain after IPv6 is disabled** (Opus). Disabling the
   family does not clear its pending state. **Status: fixed.** Pending IPv6 changes are
   discarded before an IPv4-only check, while pending IPv4 data is retained.

12. **Failed/cancelled update downloads may not be retryable** (Opus). Fixed artifact
    and helper names are reused while overwrite is refused. **Status: fixed.** A retry
    removes the prior app-managed download and adjacent AppImage helper before starting a
    fresh verified download.

13. **Linux autostart quotes paths using shell rules** (Grok). `shlex.quote()` emits
    single quotes that desktop-entry `Exec` parsing does not interpret as shell quotes;
    executable paths containing spaces may fail at sign-in. **Status: fixed.** Linux
    autostart now applies Desktop Entry argument quoting and escaping.

14. **“Check IP Now” can be dropped during a running check** (Grok). A call while
    `running` returns without preserving an immediate follow-up. **Status: fixed.** A
    manual request during a check is coalesced into one immediate run after completion.

15. **A stale file password may be used if keyring access later fails** (Grok). A
    successful keyring write leaves the old fallback file in place; a later keyring
    read failure falls through to that file. **Status: fixed.** When a usable keyring
    errors during a read, password lookup now fails closed instead of using the fallback.

## Low findings

16. **Change/start emails use version `0.0.0`** (both). The normal check worker passes a
    hard-coded version while the test-email path calls `installed_version()`.
    `src/thunderwatch/worker.py:53`. **Open.**

17. **Password fallback file has a brief permissive creation mode** (Opus). The file is
    written before `chmod(0600)` is applied. `src/thunderwatch/secrets.py:44-47`.
    **Open.**

18. **Offline setup can accept a non-empty invalid recipient** (Opus). Saving without
    testing does not validate the recipient before marking setup complete.
    `src/thunderwatch/wizard.py:208-219`. **Open.**

19. **Screenshot/UI tests may access real user settings** (Opus). The review noted
    QSettings writes outside an isolated test path and a patched state function that is
    not restored. `tests/test_screenshots.py:5-14`,
    `tools/screenshots/generate_readme_screenshots.py:14-20,44`, and
    `tests/test_qt.py`. **Open.**

20. **Source-run autostart may register the Python interpreter** (Opus). The startup
    registration path passes `sys.executable`, which is appropriate for a frozen app
    but can launch Python rather than the application on a source install.
    `src/thunderwatch/app.py:239`. **Open.**

21. **Project instruction said to describe the app as working** (Opus). The sentence in
    `AGENTS.md:4` contradicted the design's implementation-in-progress warning.
    **Status: fixed.**
