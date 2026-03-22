#!/usr/bin/env python3
"""
sekisho_sync.py — Sekisho Sync Daemon
Palm Pilot MemoDB backup via NetSync (selective database fetch).
Runs 24/7, polls continuously for Palm connections.

Part of: Sekisho Sync v1.2.2
License: MIT
"""

import fcntl
import os
import signal
import subprocess
import sys
import time
import logging
import shutil
from pathlib import Path
from datetime import datetime

from config import load_sync_config, ConfigError

VERSION = "1.2.2"

# ── Config ───────────────────────────────────────────────────────────────────
# Loaded at startup. If a variable is present but malformed, fail immediately.
try:
    _CFG = load_sync_config()
except ConfigError as _e:
    print(f"[CONFIG ERROR] {_e}", file=sys.stderr)
    sys.exit(1)

BASE_DIR           = _CFG.sekisho_base
RAW_DIR            = BASE_DIR / "raw"
TMP_DIR            = BASE_DIR / "tmp"
LOG_DIR            = BASE_DIR / "logs"
LOCKFILE           = BASE_DIR / "sekisho-sync.lock"
SLEEP_POLL         = _CFG.sleep_poll
PILOT_XFER_TIMEOUT = _CFG.timeout

# ── Logging ──────────────────────────────────────────────────────────────────
def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "sekisho-sync.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
    )
    return logging.getLogger("sekisho")

logger = setup_logger()

# ── Signal handling ──────────────────────────────────────────────────────────
stop_flag = False

def _handle_signal(signum, frame):
    global stop_flag
    stop_flag = True
    logger.info(f"Signal {signum} — shutting down...")
    # Lock released in main() finally block

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ── Lockfile (O_EXCL atomic + flock kernel-level) ────────────────────────────
_lock_fd = None

def acquire_lock() -> bool:
    """
    Two-layer lock:
      1. O_CREAT|O_EXCL  — atomic creation, no TOCTOU window
      2. fcntl.flock     — kernel auto-releases on process death (SIGKILL, crash)

    Cases handled:
      - First run           -> creates file, applies flock
      - Second live instance -> FileExistsError -> flock fails -> abort
      - Dead process (stale) -> FileExistsError -> flock succeeds -> reuse
      - Foreign user's lock  -> PermissionError -> abort without touching
    """
    global _lock_fd
    try:
        fd = os.open(str(LOCKFILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            fd = os.open(str(LOCKFILE), os.O_WRONLY)
        except PermissionError:
            logger.error("Lockfile belongs to another user. Aborting.")
            return False
        except Exception as e:
            logger.error(f"Cannot open existing lockfile: {e}")
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.warning("Stale lockfile (process dead), reusing...")
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            os.fsync(fd)
            _lock_fd = fd
            return True
        except BlockingIOError:
            try:
                pid = int(LOCKFILE.read_bytes().strip())
                logger.error(f"Another instance running (PID {pid}). Aborting.")
            except Exception:
                logger.error("Another instance running. Aborting.")
            os.close(fd)
            return False
        except Exception as e:
            logger.error(f"flock error on existing lock: {e}")
            os.close(fd)
            return False
    except PermissionError:
        logger.error(f"No permission to create lockfile in {BASE_DIR}.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error creating lockfile: {e}")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        _lock_fd = fd
        logger.debug(f"Lock acquired: PID {os.getpid()}")
        return True
    except Exception as e:
        os.close(fd)
        LOCKFILE.unlink(missing_ok=True)
        logger.error(f"flock error on new lock: {e}")
        return False

def release_lock():
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
            _lock_fd = None
            LOCKFILE.unlink(missing_ok=True)
            logger.debug("Lock released.")
        except Exception as e:
            logger.warning(f"Error releasing lock: {e}")

# ── Helpers ──────────────────────────────────────────────────────────────────
def ensure_dirs():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_orphaned_sessions()

def _cleanup_orphaned_sessions():
    if not TMP_DIR.exists():
        return
    try:
        for item in TMP_DIR.iterdir():
            if item.is_dir() and item.name.startswith("session_"):
                logger.debug(f"Cleaning orphaned session: {item.name}")
                shutil.rmtree(item)
    except Exception as e:
        logger.warning(f"Error cleaning orphaned sessions: {e}")

def _cleanup_dir(p: Path):
    if not p.exists():
        return
    try:
        shutil.rmtree(p)
    except Exception as e:
        logger.warning(f"Could not clean {p}: {e}")

def validate_pdb(pdb_path: Path) -> bool:
    """
    Validates Palm Database Format header.

    Min size: 84 bytes (78 DB header + 6 record list header).
    Consistency: size >= 84 + numRecords * 8 entry bytes.
    """
    try:
        size = pdb_path.stat().st_size
        if size < 84:
            logger.warning(f"PDB too small ({size}B < 84B minimum).")
            return False
        with open(pdb_path, "rb") as f:
            header = f.read(78)
            name = header[0:32]
            if name == b'\x00' * 32:
                logger.warning("PDB header: empty name field.")
                return False
            num_records = int.from_bytes(header[76:78], byteorder='big')
            if num_records == 0:
                logger.warning("PDB valid but empty (0 records).")
                return False
            expected_min = 84 + (num_records * 8)
            if size < expected_min:
                logger.warning(
                    f"PDB truncated: {size}B < {expected_min}B "
                    f"({num_records} records)."
                )
                return False
            db_name = name.split(b'\x00')[0].decode('latin1', errors='ignore')
            logger.debug(f"PDB OK: {num_records} records, {size}B, name={db_name}")
            return True
    except Exception as e:
        logger.error(f"PDB validation error: {e}")
        return False

def fetch_memodb(session_tmp: Path) -> bool:
    """
    Downloads MemoDB.pdb via pilot-xfer -p net:any -f MemoDB.
    Returns True only if download succeeded AND PDB passes validation.
    TimeoutExpired is normal (no Palm connected) — logged at DEBUG level.
    """
    pdb_file = session_tmp / "MemoDB.pdb"
    cmd = ["pilot-xfer", "-p", "net:any", "-f", "MemoDB"]
    try:
        logger.debug(f"Running: {' '.join(cmd)} (timeout={PILOT_XFER_TIMEOUT}s)")
        r = subprocess.run(
            cmd,
            cwd=str(session_tmp),
            capture_output=True,
            text=True,
            timeout=PILOT_XFER_TIMEOUT
        )
        if r.returncode != 0:
            logger.debug(f"pilot-xfer exit {r.returncode}")
            if r.stderr:
                logger.debug(f"stderr: {r.stderr.strip()}")
            return False
        if not pdb_file.exists():
            logger.debug("MemoDB.pdb was not created")
            return False
        if not validate_pdb(pdb_file):
            return False
        logger.info(f"MemoDB.pdb downloaded ({pdb_file.stat().st_size} bytes)")
        return True
    except subprocess.TimeoutExpired:
        logger.debug(f"pilot-xfer timeout ({PILOT_XFER_TIMEOUT}s) — no Palm connected.")
        return False
    except FileNotFoundError:
        logger.error("pilot-xfer not found. Re-run the installer to restore it.")
        return False
    except Exception as e:
        logger.error(f"pilot-xfer error: {e}")
        return False

def commit_backup(session_tmp: Path):
    """
    Moves validated MemoDB.pdb to final timestamped storage.
    Uses shutil.move() to handle cross-device moves (tmpfs -> SD card).

    Output structure:
        $SEKISHO_BASE/raw/YYYY-MM-DD/HHMMSS_ffffff/MemoDB.pdb
    """
    ts = datetime.now().strftime("%Y-%m-%d/%H%M%S_%f")
    final_dir = RAW_DIR / ts
    final_dir.mkdir(parents=True, exist_ok=True)
    src = session_tmp / "MemoDB.pdb"
    dst = final_dir / "MemoDB.pdb"
    shutil.move(str(src), dst)
    logger.info(f"Backup saved: {dst}")

# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ensure_dirs()
    if not acquire_lock():
        return 1
    try:
        logger.info(f"Sekisho Sync Daemon v{VERSION} — 24/7 mode")
        logger.info(f"Base dir:  {BASE_DIR}")
        logger.info(f"Timeout:   {PILOT_XFER_TIMEOUT}s per attempt")
        logger.info(f"Poll:      {SLEEP_POLL}s between attempts")

        while not stop_flag:
            session_id  = datetime.now().strftime("session_%Y%m%d_%H%M%S")
            session_tmp = TMP_DIR / session_id
            session_tmp.mkdir(parents=True, exist_ok=True)

            ok = fetch_memodb(session_tmp)
            if ok:
                try:
                    commit_backup(session_tmp)
                except Exception as e:
                    logger.error(f"Commit failed: {e}")
                finally:
                    _cleanup_dir(session_tmp)
            else:
                _cleanup_dir(session_tmp)

            time.sleep(SLEEP_POLL)

        logger.info("Daemon stopped.")
        return 0
    finally:
        release_lock()

if __name__ == "__main__":
    import sys
    sys.exit(main())
