## [1.2.2] - 2026-03-18

### Fixed

- **`setup_pilot_link()` library search used fixed depth and assumed subdir** —
  `find "$extract_dir/usr/lib" -maxdepth 1` only searched one level inside
  a pre-guessed path, missing libraries under `aarch64-linux-gnu` or any other
  triplet subdir. Replaced with `find "$extract_dir" -name "libpisock.so*"` and
  `libpisync.so*` across the full extract tree — no assumed subdir, no hardcoded
  architecture string, no pkgconfig path. `cp -P` and validation unchanged.

---

## [1.2.1] - 2026-03-18

### Changed

- **`setup_pilot_link()` now installs runtime libraries** — previously only
  extracted `pilot-xfer`. Now also extracts and vendorizes `libpisock.so*` and
  `libpisync.so*` from the same `.deb` into `/opt/sekisho-sync/lib`.

  - Step F: detects the lib subdir dynamically (`find /usr/lib -maxdepth 1 -type d`)
    to handle triplet paths like `aarch64-linux-gnu`; falls back to `/usr/lib`
    directly if no subdir exists. Copies matching files with `cp -P` to preserve
    symlinks.
  - Step G: validates `libpisock.so.9` present after copy; exits on missing.
  - Step H: wrapper now exports `LD_LIBRARY_PATH=/opt/sekisho-sync/lib` before
    exec, preserving any caller-set value via `${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}`.
  - Step I: runtime validation runs `LD_LIBRARY_PATH=... pilot-xfer --help` to
    confirm binary resolves all libraries before declaring success.
  - Step B idempotency updated — skips full install only if both `pilot-xfer`
    and `libpisock.so.9` are already present.

---

## [1.2.0] - 2026-03-18

### Changed

- **`pilot-link` vendorized — no longer a system dependency** — `pilot-xfer` is
  now managed entirely by the installer. It is never installed into the system
  via `apt` or `dpkg -i`. The new `setup_pilot_link()` function:
  - Downloads `pilot-link_0.12.5-5_arm64.deb` from `repo.aosc.io`
  - Extracts only `/usr/bin/pilot-xfer` using `dpkg-deb -x` (no package install)
  - Stores the binary at `/opt/sekisho-sync/bin/pilot-xfer`
  - Writes a thin wrapper at `/usr/local/bin/pilot-xfer` so `sekisho_sync.py`
    resolves it via PATH without any Python changes
  - Idempotent: skips all steps if binary already exists and is executable
  - Cleans up `/tmp/pilot-link.deb` and `/tmp/pilot-extracted` after install
  - Runs after `create_directories`, before `setup_venv`

- **`check_dependencies` no longer checks for `pilot-xfer`** — dependency on
  system `pilot-link` package removed entirely.

- **`sekisho_sync.py` error message updated** — stale `apt install pilot-link`
  hint replaced with `Re-run the installer to restore it.`

---

## [1.2.0] - 2026-03-18

### Changed

- **`pilot-link` vendored — no longer a system dependency** — `pilot-xfer` is now
  extracted from the AOSC `.deb` and stored privately at
  `/opt/sekisho-sync/bin/pilot-xfer`. A thin wrapper at `/usr/local/bin/pilot-xfer`
  delegates to it. Nothing is installed into the system package database.
  `check_dependencies` no longer checks for or mentions `pilot-xfer`.
  The installer header comment updated accordingly.

- **New `setup_pilot_link()` function** — runs after `create_directories`, before
  `setup_venv`. Steps:
  - A: creates `/opt/sekisho-sync/bin`
  - B: skips if binary already present and executable (idempotent)
  - C: downloads `pilot-link_0.12.5-5_arm64.deb` from `repo.aosc.io` via `wget`
  - D: extracts with `dpkg-deb -x` into `/tmp/pilot-extracted` — no `dpkg -i`,
       no system install, no package database touch
  - E: copies only `/usr/bin/pilot-xfer` to vendor dir, `chmod +x`
  - F: validates binary is executable; exits with clear error if not
  - Writes `/usr/local/bin/pilot-xfer` wrapper script; cleans up `/tmp` artifacts

- **`uninstall.sh` updated** — removes `/usr/local/bin/pilot-xfer` wrapper on
  uninstall. `/opt/sekisho-sync` removal already covers the vendored binary.

---

## [1.1.9] - 2026-03-18

### Added

- **`check_arch()` — ARM64 enforcement** — installer now detects architecture via
  `uname -m` immediately after `check_root`. Any non-`aarch64` system exits with
  a clear error before any dependency checks or filesystem changes are made.

- **Interactive `pilot-link` installation from AOSC** — replaces the previous
  hard-abort when `pilot-xfer` is not found. New flow:
  - Explains that `pilot-link` may not be in official repos
  - Prompts: `Install pilot-link automatically from AOSC? [Y/n]`
  - **Auto path**: downloads `pilot-link_0.12.5-5_arm64.deb` from
    `repo.aosc.io`, runs `dpkg -i`, falls back to `apt -f install` on dependency
    errors, verifies `pilot-xfer` exists after install
  - **Manual path**: prints URL, step-by-step instructions, exits with code 1
  - All failure paths exit with a clear, actionable message

---

## [1.1.8] - 2026-03-18

### Fixed

- **`migrate_config()` silent config corruption on missing trailing newline** —
  if `sekisho.conf` had no newline at end of file, every `echo "KEY=val" >> file`
  append would concatenate the new key onto the last existing line, producing
  a malformed entry that `EnvironmentFile` silently ignores. No error, no warning,
  key simply absent at runtime. Fixed by checking the last byte of the file with
  `tail -c 1 | od -An -tx1` before any append: if it is not `0a`, a newline is
  written first. The blank separator line before the comment block is now written
  in a single grouped `{ echo; echo "# ..."; echo "KEY=val"; }` redirect to
  guarantee atomicity of the three-line block.

### Changed

- **`install.sh`: "Sekisho Sync Suite" removed** — installer header, summary
  banner, and `sekisho.conf` section header now use `Sekisho Sync` consistently.
  `Sekisho Sync` is the single product name everywhere; the word "Suite" is
  retired from all runtime-visible strings.

---

## [1.1.7] - 2026-03-18

### Fixed

- **`uninstall.sh` version frozen at v1.1.3** — the uninstaller was never included
  in the version bump passes since v1.0.0. Header and banner now read v1.1.7 and
  will be included in all future version bumps.

### Changed

- **Language standardized to English throughout** — all Spanish strings removed
  from runtime output, logging, and UI across all three components:
  - `palm_memo_viewer.py`: stdout prints, log messages, error strings, HTML UI
    labels, JS alert strings, placeholder text, button labels, section comments
  - `sekisho_sync.py`: signal handler log message (`cerrando` → `shutting down`)
  - `palm_memo_extract.py`: no changes required (was already English)

- **Branding standardized to `Sekisho Sync`** — `Sekisho Sync Suite` removed from
  all `src/` component headers and `--version` output. Convention going forward:
  `Sekisho Sync` is the product name used everywhere in source code and runtime.
  `Sekisho Sync Suite` is retained only in installer/uninstaller banners and the
  `sekisho.conf` header where it describes the distributed bundle, not a component.

---

## [1.1.6] - 2026-03-18

### Fixed

- **`SyntaxError: catch without try` in `triggerExtract()`** — the `try {` block
  was accidentally dropped when replacing the `↻` icon line in v1.1.5, leaving the
  `catch(e)` clause with no matching `try`. The entire script still failed to parse.
  Restored `try {` and rewrote the arrow function callbacks in `setTimeout` as
  `function()` to eliminate any further arrow-function parsing edge cases.

---

## [1.1.5] - 2026-03-18

### Fixed

- **`SyntaxError: string literal contains an unescaped line break`** — the JS block
  in `palm_memo_viewer.py` had two compounding bugs that crashed the entire script
  before `initExtractButton()` could run, silently preventing the Sync button from
  ever appearing:

  1. `renderList()` built DOM strings via a backtick template literal that contained
     backslash-escaped single quotes (`\'active\'`). These escapes are Python
     string syntax — they are emitted as literal `\'` in the served HTML. Inside a
     JS template literal the backslash is an escape prefix, producing an invalid
     token that some browsers parse as an unescaped line break error.
     Fixed by rewriting `renderList()` with plain string concatenation — no
     backtick template literal, no escaped quotes.

  2. Several JS single-quoted string literals contained raw non-ASCII characters
     (`↻` U+21BB, `✓` U+2713, `✗` U+2717, `ó` U+00F3, `·` U+00B7). When the
     browser's JS parser encountered these mid-token, it threw a SyntaxError
     depending on the inferred encoding context.
     Fixed by replacing all non-ASCII in JS string literals with `\uXXXX` escapes
     or ASCII equivalents. Non-ASCII in HTML (the `↻` in the `<span>` tag) is
     unaffected — that is HTML content, not a JS token.

---

## [1.1.4] - 2026-03-18

### Fixed

- **Sync button never visible on upgraded installs** — `write_config()` preserves
  existing `sekisho.conf` on upgrades (correct behavior). But installs prior to
  v1.0.2 had no `EXTRACT_SCRIPT` line, so the viewer always received an empty
  string → `extract_script = None` → `ExtractService.available = False` →
  `GET /api/extract/status` returned `{"available": false}` → `initExtractButton()`
  never called `classList.remove('hidden')`. The Sync button was permanently hidden
  even though `palm_memo_extract` was installed and functional.

- **`migrate_config()` added to installer** — runs after `write_config()` on every
  install. Checks for `EXTRACT_SCRIPT`, `EXTRACT_ARGS`, and `EXTRACT_TIMEOUT` keys
  individually; appends any that are missing with a dated comment. Idempotent: a
  config already containing all keys is not modified. Covers all three keys absent
  from pre-v1.0.2 installs.

---

## [1.1.3] - 2026-03-17

### Changed

- **README documents architecture model explicitly** — added "always-on / on-demand
  hybrid" description: two 24/7 services, one ephemeral CLI, filesystem-only IPC.
- **README documents determinism** — pinned dependencies including `pip==24.3.1`
  described as a design property, not an implementation detail.
- **README documents self-healing installer** — idempotent validation, destroy-and-
  recreate on failure, always-reconcile behavior now explicitly described.

---

## [1.1.2] - 2026-03-17

### Fixed

- **README updated to reflect current installer behavior**
  - Requirements: `Python 3.7+` → `Python 3.8+` with `venv` module
  - Removed `pip3 install flask` mention — Flask is never installed globally
  - Added note: all dependencies installed in isolated venv, no system pip
  - Installation section: describes venv creation and pinned dependency install
  - Project structure: added `requirements.txt` and `config.py` entries

---

## [1.1.1] - 2026-03-17

### Fixed

- **pip self-pin uses `python -m pip` instead of `pip`** — avoids edge cases where
  the old bundled pip binary behaves differently from the module.
  `$VENV_PYTHON -m pip install pip==24.3.1` is the canonical way to upgrade pip
  from within a venv.

---

## [1.1.0] - 2026-03-17

### Changed

- **pip version pinned for full determinism** — `requirements.txt` now includes
  `pip==24.3.1`. The venv installer pins pip to this exact version before running
  `pip install -r requirements.txt`. Previously `--upgrade pip` pulled latest pip,
  leaving one variable unpinned. Now the entire Python environment is reproducible:
  pip, flask, and all transitive dependencies at exact known versions.

---

## [1.0.9] - 2026-03-17

### Fixed

- **Python version validated before install** — added check in `check_dependencies()`:
  Python 3.8+ required (Flask 3.x minimum). Installer aborts with clear message
  if system Python is too old. Reports detected version and minimum required.
- **pip upgraded inside venv** — `$VENV_PIP install --upgrade pip` runs in Step 2,
  after venv creation and before `requirements.txt` install. Prevents obscure install
  failures on systems shipping old pip (Raspberry Pi OS Bullseye, Buster, etc.).

---

## [1.0.8] - 2026-03-17

### Changed

- **`setup_venv()` redesigned — validate-or-rebuild + always reconcile**

  Step 1 — Validate existing venv (do not trust presence alone):
  - Checks python binary executes: `$VENV_PYTHON -c "import sys; sys.exit(0)"`
  - Checks pip is functional: `$VENV_PIP --version`
  - If either check fails: destroy venv completely (`rm -rf $VENV_DIR`)
  - No partial repair attempts. Broken → gone.

  Step 2 — Create venv if absent (fresh or just destroyed):
  - `python3 -m venv $VENV_DIR`
  - Aborts with clear error if creation fails

  Step 3 — Always reconcile with `requirements.txt`:
  - `pip install -r requirements.txt` runs on every install, even if venv was healthy
  - Idempotent: correct versions → no-op, missing → installs, drift → corrects
  - Aborts with debug hint if pip fails

  Step 4 — Validate critical import:
  - `$VENV_PYTHON -c "import flask"`
  - If fails after install: aborts with debug command

---

# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

---

## [1.0.7] - 2026-03-17

### Changed

- **Installer redesigned for deterministic venv-based deployment**
  - Creates isolated Python venv at `/opt/sekisho-sync/venv`
  - All Python dependencies installed inside the venv only — no apt/dnf/pacman,
    no `pip install --break-system-packages`, no touching system Python
  - Both systemd services use `ExecStart=/opt/sekisho-sync/venv/bin/python ...`
  - `palm_memo_extract` CLI wrapper uses venv Python
  - venv creation is idempotent: skipped if already exists

- **`requirements.txt` added** — all 7 packages pinned to exact versions:
  flask, werkzeug, jinja2, click, itsdangerous, markupsafe, blinker

- **Installer fail-fast on dependency error** — if `pip install -r requirements.txt`
  fails for any reason, installer aborts immediately with a clear error message
  and debug hint. No partial installations.

- **Simplified dependency check** — removed multi-distro Flask install logic
  (apt/dnf/pacman/pip cascade). System only needs `python3` and `python3-venv`.
  Everything else comes from the venv.

- **`palm_memo_extract` CLI** changed from symlink to wrapper script that explicitly
  calls the venv Python.

---

## [1.0.6] - 2026-03-17

### Fixed

- **`CFG`, `SERVICE`, `EXTRACT` moved out of import-time** — were instantiated at
  module level, meaning `ConfigError` would raise before entering `__main__`. The
  `try/except` was effectively unreachable for config errors. All three now
  initialized inside the `try` block in `__main__`.
- **Duplicate `except ConfigError` in `__main__` removed** — two consecutive except
  blocks for the same exception. Collapsed to one.
- **`sys` import moved before use** — `sys` is now imported at the top of `__main__`
  before the `try`, not inside the second (now removed) except block.
- **CHANGELOG stale duplicate removed** — prior version bump script was converting
  the previous entry's header to the new version, creating two identical headers.
- **`load_config()` docstring corrected** — falsely stated `PAGE_SIZE`, `MAX_PAGE_SIZE`,
  `PREVIEW_CHARS` were not part of the shared contract. They are, since v1.0.4.

---

## [1.0.5] - 2026-03-17

### Fixed

- **CHANGELOG duplicate `[1.0.4]` entry removed** — version bump script converted
  the previous entry header, creating two identical `[1.0.4]` blocks.
- **`load_config()` docstring corrected** — falsely stated `PAGE_SIZE`, `MAX_PAGE_SIZE`,
  `PREVIEW_CHARS` were viewer-internal and not part of the shared contract.

### Known gaps (fixed in 1.0.7)

- `CFG`, `SERVICE`, `EXTRACT` still instantiated at import-time
- Duplicate `except ConfigError` still present in `__main__`
- `sys` still referenced before import in second except block

---

## [1.0.4] - 2026-03-17

### Fixed

- **`config.py` duplicate fields** — `PAGE_SIZE`, `MAX_PAGE_SIZE`, `PREVIEW_CHARS` duplicated in registry docstring and `ViewerConfig` dataclass. Removed.
- **`HOST` validation** — replaced non-empty check with `_parse_host()`: IPv4 or `localhost` only.
- **`palm_memo_extract.py` docstring** — removed stale `/sekisho/raw/` and `Sekisho Sync Daemon v1.1.0+` references.
- **CHANGELOG** — rewritten to reflect actual state, with known-gaps sections per release.

### Known gaps (fixed in 1.0.7)

- CHANGELOG still had duplicate `[1.0.4]` entry after version bump script
- `load_config()` docstring still contradicted the contract for the three viewer vars

---

## [1.0.3] - 2026-03-17

### Fixed

- **`sys.exit()` removed from viewer `load_config()`** — `ConfigError` propagates to `__main__`.
- **`PAGE_SIZE`, `MAX_PAGE_SIZE`, `PREVIEW_CHARS` added to config contract** — validated via `_parse_int()`, no silent clamps.
- **`VIEWER_HOST`/`VIEWER_PORT` purged from docs** — README and ARCHITECTURE now consistently use `HOST` and `PORT`.
- **Viewer branding residue removed** — legacy identity string replaced.
- **`palm_memo_extract.py` docstring** — stale path and daemon version reference updated.
- **Flask install order** — `apt` first, `pip3` as last resort.

### Known gaps (fixed in 1.0.4)

- `config.py` still had duplicate entries for three variables
- `HOST` validation still too permissive
- `load_config()` docstring still contradicted the contract

---

## [1.0.2] - 2026-03-17

### Added

- **`config.py` — shared configuration contract**
  - `ConfigError` exception raised on malformed variables; callers handle termination
  - `load_sync_config()` for `sekisho_sync.py`
  - `load_viewer_config()` for `palm_memo_viewer.py`
  - Rule: absent variable uses silent default. Present but malformed raises immediately.
  - `EXTRACT_SCRIPT` validated for existence and executability if non-empty

### Changed

- `sekisho_sync.py` — replaced inline `os.getenv()` with `load_sync_config()`
- `palm_memo_viewer.py` — `load_config()` delegates to `load_viewer_config()`
- `palm_memo_extract.py` — fixed stale `SEKISHO_BASE` default (`/sekisho` to `/var/lib/sekisho`)
- `install.sh` — copies `config.py` to install dir, validates its presence

### Known gaps (fixed in 1.0.7)

- `sys.exit()` still present inside `load_config()` — violating the design rule
- `PAGE_SIZE`, `MAX_PAGE_SIZE`, `PREVIEW_CHARS` not yet part of the contract
- `HOST` only validated as non-empty — too permissive
- `config.py` docstring duplicated three variable entries

---

## [1.0.1] - 2026-03-17

### Fixed

- **`HOST`/`PORT` env var mismatch** — installer was writing `VIEWER_HOST` and
  `VIEWER_PORT` to `sekisho.conf`, but viewer reads `HOST` and `PORT`. Config
  had zero effect on viewer network binding.
- **`MEMOS_DIR` default in viewer** — was `/memo/extract`. Corrected to
  `/var/lib/sekisho/extract`.
- **Version string in viewer startup** — said `v3.3.0`, corrected to `v3.4.0`.

### Known gaps (partially fixed in 1.0.7)

- `VIEWER_HOST`/`VIEWER_PORT` naming corrected in code but not fully purged from docs

---

## [1.0.0] - 2026-03-17

Initial release. Unified package integrating three previously separate tools.

### Components

- `sekisho_sync.py` — sync daemon (24/7, no time windows)
- `palm_memo_extract.py` — PDB extractor CLI (on-demand)
- `palm_memo_viewer.py` — Flask web viewer (24/7)

### Architecture

Three-component pipeline with filesystem as the only shared state:

```
Palm TX -> sekisho-sync (daemon) -> /var/lib/sekisho/raw/.../MemoDB.pdb
        -> palm_memo_extract (CLI) -> /var/lib/sekisho/extract/*.txt
        -> sekisho-viewer (daemon) -> http://<host>:5000
```

### Changes from standalone sekisho_sync versions

- Removed time window restriction — daemon now runs 24/7
- Data directory: `/sekisho` to `/var/lib/sekisho` (FHS compliant)
- All tunable values moved to environment variables

### Installer

- Single `install.sh` deploys all three components
- Two systemd services: `sekisho-sync` and `sekisho-viewer`
- Shared config: `/etc/sekisho-sync/sekisho.conf`
- Dedicated `sekisho` system user by default (`--no-create-user` requires confirmation)
- systemd hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`
- logrotate owner matches daemon user

---

## Roadmap

### [1.1.0]
- [ ] Automatic extraction triggered after successful sync (no manual button needed)
- [ ] Systemd timer for scheduled extraction
- [ ] Skip extraction if PDB SHA256 unchanged

### [1.2.0]
- [ ] Multi-database support (AddressDB, DatebookDB, etc.)
- [ ] GitHub repository published with real URLs

### [2.0.0]
- [ ] .deb package for apt install