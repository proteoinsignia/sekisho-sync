# Architecture

## Pipeline

```
Palm Pilot
    │
    │  NetSync (TCP port 14238)
    ▼
sekisho_sync.py  ──────────────────────────────  [systemd: sekisho-sync, 24/7]
    │
    │  Polls pilot-xfer every SEKISHO_SLEEP_POLL seconds
    │  On success: validates PDB, moves to timestamped archive
    │
    ▼
$SEKISHO_BASE/raw/YYYY-MM-DD/HHMMSS_ffffff/MemoDB.pdb
    │
    │  (on-demand: triggered from viewer UI or CLI)
    ▼
palm_memo_extract.py  ─────────────────────────  [CLI, ephemeral subprocess]
    │
    │  --sekisho flag: auto-detects latest PDB in raw/
    │  Parses PDB records, writes .txt files
    │  Writes index.json with metadata
    │
    ▼
$SEKISHO_BASE/extract/
    ├── YYYY-MM-DD_r00001_Title.txt
    ├── YYYY-MM-DD_r00002_Title.txt
    └── index.json
    │
    │  (viewer watches this directory)
    ▼
palm_memo_viewer.py  ──────────────────────────  [systemd: sekisho-viewer, 24/7]
    │
    │  Flask server
    │  MemoService: caches .txt list, serves search + content
    │  ExtractService: runs palm_memo_extract as subprocess on demand
    │
    ▼
http://<host>:$PORT
```

## Component Responsibilities

### sekisho_sync.py
- Polls `pilot-xfer -p net:any -f MemoDB` continuously
- Validates PDB structure before committing
- Stores raw PDB backups (never deletes)
- Knows nothing about extraction or viewer

### palm_memo_extract.py
- Stateless CLI tool — reads PDB, writes .txt files
- `--sekisho` mode auto-detects latest PDB from sync archive
- Optional SHA256 state tracking to skip unchanged PDBs
- Optional memo deduplication via JSON state file
- Knows about Sekisho's raw/ directory structure, nothing about viewer

### palm_memo_viewer.py
- Reads .txt files from extract directory (read-only)
- In-memory cache with TTL (no database)
- Triggers palm_memo_extract via subprocess (EXTRACT_SCRIPT env var)
- Knows about extract directory and extract CLI, nothing about raw/ PDBs

## Shared State (filesystem only)

```
/var/lib/sekisho/
├── raw/           written by: sekisho_sync    read by: palm_memo_extract
├── extract/       written by: palm_memo_extract  read by: palm_memo_viewer
├── logs/          written by: sekisho_sync
└── tmp/           written by: sekisho_sync (transient, auto-cleaned)
```

No IPC, no shared memory, no message queue. Components communicate only via files.

## Configuration

Both systemd services use `EnvironmentFile=/etc/sekisho-sync/sekisho.conf`.
This single file is the source of truth for all paths and tunables.

## Lockfile Strategy

`sekisho_sync.py` uses a two-layer lock:
1. `O_CREAT | O_EXCL` — atomic file creation (no TOCTOU race)
2. `fcntl.flock(LOCK_EX)` — kernel releases automatically on process death

`palm_memo_extract.py` has its own optional `--lock` flag using the same flock pattern.
The two lockfiles are independent and never conflict.

## Security Model

- Both daemons run as `sekisho` system user (no login shell, no home access)
- systemd `ProtectSystem=strict` makes filesystem read-only except `ReadWritePaths`
- `NoNewPrivileges=true` blocks SUID escalation
- `PrivateTmp=true` isolates /tmp
- NetSync listens on 14238 — consider firewall rules to restrict to LAN only
