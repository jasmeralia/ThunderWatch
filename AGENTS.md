# ThunderWatch project instructions

Read [docs/DESIGN.md](docs/DESIGN.md) before changing change detection, email
notification, startup flow, packaging, or CI. This is a design-stage repository; do not
describe it as a working IP monitor until the acceptance checks are implemented and
tested.

ThunderWatch is part of the Storm Desktop Suite (GaleFling, StormFuse, TempestTrace).
Reuse their conventions; TempestTrace (`../TempestTrace`) is the primary template for
tooling, theme, updater, packaging, and workflows. Keep the theme tokens and the app icon
identical to the rest of the suite.

The core rule: a failed IP lookup is never an IP change. Keep `ipcheck`, `monitor`,
`notifier`, `state`, `config`, and `secrets` independent of Qt, and test them with fakes.
Tests must never contact real IP providers or SMTP servers. Never log, persist to
QSettings, or email the SMTP password, and never commit real SMTP credentials.

Use test-driven development for behavior changes: add a failing synthetic test,
implement the behavior, then refactor with the tests green. Add a regression test before
fixing a discovered bug.

Before a code PR, run `make lint` and `make test`. Keep README and design docs current
with user-facing changes. When a PR visibly changes the status window, tray menu,
settings dialog, or setup wizard, run `make screenshots` and commit the regenerated
`docs/images/` files in the same PR. Screenshots must use synthetic data only (example.com
accounts, documentation IP ranges), never real addresses or credentials.

Successful master builds publish beta prereleases. Morgan promotes a validated
prerelease to a full release manually using the same tag and assets. CI must never
promote a release or downgrade one back to prerelease on rerun. The updater ignores
prereleases unless the user enabled "Include beta updates".

PRs require `Lint & Test`, `codecov/project`, and `codecov/patch` (once CI exists), a
completed Copilot review, and resolved review conversations. Merge with squash. Keep
pytest-cov's `coverage.xml` and pytest's `junit.xml` uploads working. Dependabot
auto-merge uses the dedicated `DEPENDABOT_MERGE_TOKEN` Dependabot secret so its master
merge triggers release CI; an Actions secret is not exposed to Dependabot-triggered
workflows.
